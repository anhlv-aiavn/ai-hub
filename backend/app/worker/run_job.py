"""Xử lý 1 GCN (1 PDF): tải PDF từ MinIO → detect (windowed) + extract (song song,
có trần VLM toàn cục) → normalize → cắt file (tái dùng ảnh đã render) → ghi Mongo + SSE.

Concurrency:
- Worker (app/worker/main.py) chạy NHIỀU file in-flight cùng lúc.
- `_VLM_SEM` (semaphore toàn cục) bao MỌI call detect + extract → bơm nhiều request
  đồng thời cho vLLM dynamic-batch nhưng có trần `MAX_VLM_CONCURRENT`.

File lớn:
- > AIHUB_MAX_PAGES (mặc định 250) → skip (an toàn RAM/thời gian).
- Detect = phân loại biên TỪNG TRANG (cover/content/other) song song rồi suy nhóm
  tuyến tính (quyết định cục bộ → không lỗi mốc cửa sổ, không rớt trang)."""

import asyncio
import base64
import functools
import io
import logging
import os
import time
from datetime import datetime, timezone

import litellm
from PIL import Image

from app import config, storage
from app.batch_counters import bump
from app.bus import publish_sync
from app.storage import DestinationNotConfigured, SourceObjectMissing, SourceObjectUnavailable
from app.sph_ten_tep import va_sph_tu_ten_tep
from app.summary import collect_so_phat_hanhs, group_key_of, per_gcn, summarize
from src.extentions.mongo_helper import AsyncMongo
from src.extentions.multimodal.detect_gcn import classify_page, groups_from_roles
from src.extentions.multimodal.extract_gcn import extract
from src.extentions.multimodal.make import (
    count_pdf_pages_from_bytes,
    pdf_to_corrected_images,
)
from src.extentions.multimodal.chu_cuoi import ALGO_VERSION as CHU_CUOI_VERSION
from src.extentions.multimodal.chu_cuoi import chu_cuoi_for_entry
from src.extentions.multimodal.mdsdd import ALGO_VERSION as MDSDD_VERSION
from src.extentions.multimodal.mdsdd import gan_mdsdd
from src.extentions.multimodal.normalize_dang_ky import normalize_extractions
from src.extentions.multimodal.vlm_client import total_vlm_concurrency

log = logging.getLogger(__name__)

DETECT_MIN_PAGES = int(os.getenv("DETECT_MIN_PAGES", "5"))
RENDER_DPI = int(os.getenv("AIHUB_RENDER_DPI", "200"))
RENDER_MAX_SIZE = int(os.getenv("AIHUB_RENDER_MAX_SIZE", "2000"))
MAX_PAGES = int(os.getenv("AIHUB_MAX_PAGES", "250"))
EXTRACT_TIMEOUT = float(os.getenv("EXTRACT_TIMEOUT_SECONDS", "3000"))

# Semaphore TOÀN CỤC cho mọi call VLM (detect+extract). Tạo lazy trong event loop worker.
_VLM_SEM: asyncio.Semaphore | None = None


def _vlm_sem() -> asyncio.Semaphore:
    global _VLM_SEM
    if _VLM_SEM is None:
        # Trần TỔNG = năng lực cả pool (trần/máy × số endpoint). Cân tải + trần
        # riêng TỪNG máy đã do vlm_client lo; semaphore này chỉ chặn fan-out để
        # bound RAM (mỗi call đang chờ giữ ảnh base64 ~vài MB). Thêm 1 máy vLLM
        # vào VLLM_ENDPOINTS là trần này tự nhân lên, không cần sửa gì ở đây.
        _VLM_SEM = asyncio.Semaphore(total_vlm_concurrency())
    return _VLM_SEM


def _friendly_vlm_error(e: Exception) -> str:
    """Dịch lỗi gọi VLM (litellm) thành thông báo tiếng Việt cho người dùng cuối
    thay vì để lộ nguyên văn traceback kỹ thuật (vd "litellm.InternalServerError:
    InternalServerError: OpenAIException - Connection error."). Traceback thật đã
    được ghi bởi `log.exception` ở nơi gọi — hàm này chỉ đổi message LƯU VÀO DOC."""
    low = str(e).lower()
    # Kiểm tra message trước isinstance: server vLLM tự host thường trả lỗi kết nối
    # dưới dạng litellm.InternalServerError (không phải APIConnectionError chuẩn).
    if "connection error" in low or "connect" in low:
        return "Không kết nối được tới hệ thống nhận diện (VLM). Vui lòng thử lại sau ít phút."
    if isinstance(e, litellm.RateLimitError):
        return "Hệ thống nhận diện đang quá tải, vui lòng thử lại sau."
    if isinstance(e, litellm.AuthenticationError):
        return "Lỗi xác thực với hệ thống nhận diện — liên hệ quản trị viên."
    if isinstance(e, (litellm.ContentPolicyViolationError, litellm.ContextWindowExceededError)):
        return "Không xử lý được nội dung tệp (bị hệ thống nhận diện từ chối hoặc vượt giới hạn)."
    if isinstance(e, litellm.APIError):
        return "Hệ thống nhận diện gặp sự cố nội bộ, vui lòng thử lại sau."
    return str(e)


# ── Pipeline ────────────────────────────────────────────────────────────────

async def _detect_groups(images: list[str]) -> list[list[int]]:
    """Detect = phân loại biên TỪNG TRANG rồi suy nhóm tuyến tính.

    File rất ngắn (≤ DETECT_MIN_PAGES) → coi là MỘT giấy, khỏi gọi VLM. Còn lại:
    phân loại song song mỗi trang (cover/content/other) — mỗi call 1 ảnh nên chính
    xác cao + batch tốt, KHÔNG còn lỗi mốc cửa sổ / nhồi nhiều ảnh / rớt trang."""
    n = len(images)
    if n == 0:
        return []
    if n <= DETECT_MIN_PAGES:
        return [list(range(n))]

    async def _cls(i: int) -> str:
        async with _vlm_sem():
            try:
                return await classify_page(images[i])
            except Exception as e:  # noqa: BLE001
                log.warning("classify_page trang %d lỗi: %s", i, e)
                return "content"  # fail-safe: giữ trang, không cắt nhầm

    roles = await asyncio.gather(*(_cls(i) for i in range(n)))
    return groups_from_roles(list(roles))


async def _pipeline(pdf_buf: io.BytesIO, timings: dict | None = None) -> tuple[list[dict], list[str]]:
    """Trả (records, images). images = ảnh đã xoay thẳng (tái dùng để cắt file).
    `timings` (nếu truyền) được điền render/detect/extract để bóc tách nút thắt."""
    loop = asyncio.get_running_loop()
    n = await loop.run_in_executor(None, count_pdf_pages_from_bytes, pdf_buf)
    if n == 0:
        return [], []
    if n > MAX_PAGES:
        return [{"page_indices": [], "result": None, "error": f"too_many_pages:{n}",
                 "page_count": n, "skip_reason": "too_many_pages"}], []

    render = functools.partial(
        pdf_to_corrected_images, dpi=RENDER_DPI, max_img_size=RENDER_MAX_SIZE
    )
    _t = time.monotonic()
    images = await loop.run_in_executor(None, render, pdf_buf)
    if timings is not None:
        timings["render"] = round(time.monotonic() - _t, 3)
    if not images:
        return [], []
    page_count = len(images)
    _t = time.monotonic()
    groups = await _detect_groups(images)
    if timings is not None:
        timings["detect"] = round(time.monotonic() - _t, 3)
    if not groups:
        return [], images

    # Đo TÁCH BẠCH: chờ slot VLM (hàng đợi nội bộ) vs gọi thật. Gộp chung thì
    # timings["extract"] = chờ + gọi, nhìn ra 1720s trong khi timeout là 800s và
    # tưởng nhầm là một call chạy quá giờ.
    cho_slot: list[float] = []
    goi_that: list[float] = []

    async def _run(group: list[int]) -> dict:
        base = {"page_indices": group, "result": None, "error": None,
                "page_count": page_count, "skip_reason": None}
        imgs = [images[i] for i in group if 0 <= i < len(images)]
        _t_cho = time.monotonic()
        try:
            async with _vlm_sem():
                cho_slot.append(time.monotonic() - _t_cho)
                _t_goi = time.monotonic()
                try:
                    base["result"] = await asyncio.wait_for(extract(imgs), timeout=EXTRACT_TIMEOUT)
                finally:
                    goi_that.append(time.monotonic() - _t_goi)
        except asyncio.TimeoutError:
            base["error"] = f"timeout>{EXTRACT_TIMEOUT}s"
        except Exception as e:  # noqa: BLE001
            log.exception("extract lỗi pages %s: %s", group, e)
            base["error"] = _friendly_vlm_error(e)
        return base

    _t = time.monotonic()
    records = await asyncio.gather(*(_run(g) for g in groups))
    if timings is not None:
        timings["extract"] = round(time.monotonic() - _t, 3)
        # cho_slot cao = GPU bão hoà, hạ MAX_VLM_CONCURRENT/MAX_IN_FLIGHT chứ
        # không phải nới EXTRACT_TIMEOUT. goi_that cao = call thật sự chậm
        # (thinking, ảnh to) → hạ max_tokens hoặc tắt thinking.
        if cho_slot:
            timings["cho_slot"] = round(max(cho_slot), 3)
        if goi_that:
            timings["goi_that"] = round(max(goi_that), 3)
    return records, images


# ── Cắt file (tái dùng ảnh đã render) ───────────────────────────────────────

def _images_to_pdf(b64_list: list[str]) -> bytes | None:
    pages = []
    for b in b64_list:
        try:
            pages.append(Image.open(io.BytesIO(base64.b64decode(b))).convert("RGB"))
        except Exception:  # noqa: BLE001
            continue
    if not pages:
        return None
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:])
    return buf.getvalue()


def _entry_sph(rec: dict) -> str | None:
    res = rec.get("result") if isinstance(rec, dict) else None
    if isinstance(res, dict):
        for e in res.get("Đăng ký", []) or []:
            gcn = e.get("Giấy chứng nhận") if isinstance(e, dict) else None
            if isinstance(gcn, dict) and gcn.get("Số phát hành"):
                return str(gcn["Số phát hành"])
    return None


async def _build_cuts(gcn_id: str, batch_id, images: list[str], records: list,
                      dest_purpose: str = "gcn", naming_fn=None, correct_fn=None) -> list[dict]:
    """`dest_purpose` chọn ĐÍCH nào nhận file cắt (mặc định "gcn" — pipeline
    chính, không đổi hành vi cũ). Pipeline QC Sync (app/worker/qc_pipeline.py)
    gọi hàm này với `dest_purpose="qc"` để ghi vào S3 đích RIÊNG của nó, dùng
    `gcn_id`/`batch_id` chỉ như thành phần đặt tên key mặc định (không phải
    doc `gcn` thật) — xem storage._get_dest_client.

    `naming_fn(ri, sph, stem) -> (s3_key, display_name)` — nếu truyền, GHI ĐÈ
    quy ước đặt khoá S3/tên hiển thị mặc định bên dưới (dùng cho QC Sync: KHÔNG
    tạo thư mục con, tên file = tên GCN + tên file gốc + "_cropped").

    `correct_fn(pdf_bytes, ri) -> (pdf_bytes, extra)` — nếu truyền, gọi NGAY
    TRƯỚC KHI ghi lên S3 đích để "làm đẹp" bản cắt (vd QC Sync dùng QC-2, xem
    `qc_pipeline._qc2_correct`); `pdf_bytes` trả về THAY THẾ bản cắt gốc,
    `extra` (dict hoặc None) được gộp thêm vào entry `cuts[]` tương ứng.
    Mặc định None — hành vi pipeline GCN chính KHÔNG đổi."""
    cuts: list[dict] = []
    for ri, rec in enumerate(records):
        pages = rec.get("page_indices") if isinstance(rec, dict) else None
        if not pages:
            continue
        group = [images[j] for j in pages if isinstance(j, int) and 0 <= j < len(images)]
        pdf_bytes = _images_to_pdf(group)
        if not pdf_bytes:
            continue
        extra = None
        if correct_fn:
            try:
                pdf_bytes, extra = await correct_fn(pdf_bytes, ri)
            except Exception as e:  # noqa: BLE001 — 1 cut lỗi correct_fn KHÔNG được xoá cả
                # cuts còn lại của item (đã từng gặp thật với QC Sync — xem
                # qc_pipeline._qc2_correct); giữ bản cắt gốc, bỏ qua extra.
                log.warning("_build_cuts %s cut %s: correct_fn lỗi, giữ bản cắt gốc: %s",
                           gcn_id, ri, e)
                extra = None
        sph = _entry_sph(rec)
        # Tên tệp cắt chuẩn: "<Số phát hành>-GCN.pdf". Thiếu Số phát hành → kèm
        # index để khỏi trùng giữa các bản cắt cùng file.
        stem = sph if sph else f"{gcn_id}-{ri + 1}"
        if naming_fn:
            ckey, name = naming_fn(ri, sph, stem)
        else:
            ckey, name = f"{batch_id}/{gcn_id}/cut-{ri}.pdf", f"{stem}-GCN.pdf"
        await storage.put_pdf(ckey, pdf_bytes, purpose=dest_purpose)
        cut = {
            "index": ri, "s3_key": ckey, "page_indices": pages,
            "page_count": len(group), "so_phat_hanh": sph, "name": name,
        }
        if extra:
            cut.update(extra)
        cuts.append(cut)
    return cuts


async def _refresh_dup_group(mongo: AsyncMongo, gcn_id: str, sph_list: list[str]) -> tuple[bool, list[str]]:
    """Tính LẠI TOÀN BỘ (không cộng dồn) danh sách hồ sơ khác cùng chia sẻ ít
    nhất 1 Số phát hành với `gcn_id`, rồi GHI ĐÈ (không $addToSet). Quan hệ này
    đối xứng tự nhiên (X giao Y ≠ rỗng ⇔ Y giao X ≠ rỗng) và không giới hạn số
    lượng kết quả.

    Trước đây: mỗi doc chỉ tự tính 1 LẦN lúc xử lý xong, cap tối đa 5 kết quả
    (chiều tiến), rồi các doc TÌM THẤY được cộng dồn thêm chính doc mới vào
    danh sách của HỌ qua $addToSet (chiều ngược, không cap) — 2 chiều khác cơ
    chế nhau nên 2 hồ sơ "trùng nhau" hoàn toàn có thể ra số lượng khác nhau,
    tùy thời điểm/thứ tự xử lý (bug thực tế người dùng gặp: hồ sơ 1 thấy trùng
    5, hồ sơ 2 lại thấy trùng 7). Tính lại toàn bộ + ghi đè cho MỌI hồ sơ liên
    quan mỗi lần có thay đổi giúp tự "chữa lành" luôn dữ liệu lệch cũ, không
    cần script backfill riêng."""
    others = await mongo.db[config.COLL_GCN].find(
        {"extracted_so_phat_hanhs": {"$in": sph_list}, "_id": {"$ne": gcn_id}},
        {"_id": 1},
    ).to_list(length=None)
    ids = [d["_id"] for d in others]
    await mongo.update_one(
        config.COLL_GCN, {"_id": gcn_id},
        {"$set": {"dup_suspect": bool(ids), "dup_candidates": ids}},
    )
    return bool(ids), ids


# ── Orchestration cho 1 doc đã được worker claim ────────────────────────────

def _chu_cuoi_records(records: list) -> list[dict]:
    """Danh sách chu_cuoi (1/entry Đăng ký) — REGEX-only, cùng định dạng backfill."""
    out: list[dict] = []
    for ri, rec in enumerate(records or []):
        if not isinstance(rec, dict):
            continue
        res = rec.get("result")
        if not isinstance(res, dict):
            continue
        for ei, e in enumerate(res.get("Đăng ký") or []):
            if not isinstance(e, dict):
                continue
            gcn = e.get("Giấy chứng nhận")
            sph = str(gcn.get("Số phát hành", "")) if isinstance(gcn, dict) else ""
            out.append({"rec_index": ri, "entry_index": ei, "so_phat_hanh": sph,
                        **chu_cuoi_for_entry(e)})
    return out


async def process_doc(mongo: AsyncMongo, doc: dict) -> str:
    """Xử lý 1 gcn doc (đã set processing bởi worker). Dùng chung mongo client."""
    gcn_id = doc["_id"]
    batch_id = doc.get("batch_id")
    branch = doc.get("branch")  # nhúng vào event để SSE lọc theo chi nhánh

    # SSE gộp mức lô (§Quy mô cực lớn 3): lô LỚN bỏ ping per-doc "processing"/
    # "done" bình thường (event "batch" gộp ở _rollup là nguồn tiến độ) — lỗi thì
    # LUÔN bắn (không mất toast lỗi). Lô nhỏ giữ nguyên hành vi cũ (mọi event bắn).
    batch_doc = await mongo.db[config.COLL_BATCH].find_one(
        {"_id": batch_id}, {"file_count": 1}) if batch_id else None
    is_large = bool(batch_doc and (batch_doc.get("file_count") or 0) > config.SSE_AGG_FILE_THRESHOLD)

    if not is_large:
        publish_sync({"type": "gcn", "gcn_id": gcn_id, "batch_id": batch_id,
                      "branch": branch, "status": "processing"})

    # Đo thời gian từng chặng (gated AIHUB_TRACE_TIMINGS) — bóc tách nút thắt lúc
    # chạy: download S3 vs pipeline (render+VLM) vs ghi cut. Tắt → timings=None,
    # zero overhead.
    timings: dict | None = {} if config.TRACE_TIMINGS else None

    try:
        # source_connection_id=None → hành vi cũ (kho nội bộ). Có id → đọc kho
        # nguồn read-only; lỗi (mất/di chuyển/quyền) raise SourceObjectUnavailable,
        # bắt chung bên dưới → doc "error" message rõ, không kẹt processing.
        _t = time.monotonic()
        pdf_buf = await storage.get_pdf(doc["s3_key"], doc.get("source_connection_id"))
        if timings is not None:
            timings["download"] = round(time.monotonic() - _t, 3)
        if pdf_buf.getvalue()[:4] != b"%PDF":
            raise ValueError("File không phải PDF (magic-byte không khớp)")
        _t = time.monotonic()
        records, images = await _pipeline(pdf_buf, timings)
        if timings is not None:
            timings["pipeline"] = round(time.monotonic() - _t, 3)
    except Exception as e:  # noqa: BLE001
        log.exception("process %s lỗi: %s", gcn_id, e)
        # Phân loại lỗi:
        #  - File KHÔNG tồn tại (NoSuchKey) → "no_file": lỗi vĩnh viễn, tách khỏi
        #    "Lỗi" để KHÔNG bị nút retry quét (chạy lại vẫn NoSuchKey) — OPS-1.
        #    SourceObjectMissing là con của SourceObjectUnavailable → CHECK TRƯỚC.
        #  - "permanent" (không phải PDF) không đáng retry tự động.
        #  - "transient" (nguồn tạm mất/timeout/lỗi khác) mặc định retry được.
        if isinstance(e, SourceObjectMissing):
            fail_status, error_kind = "no_file", "missing_source"
        elif isinstance(e, ValueError):
            fail_status, error_kind = "error", "permanent"
        else:  # SourceObjectUnavailable (tạm) + lỗi khác
            fail_status, error_kind = "error", "transient"
        await mongo.update_one(
            config.COLL_GCN, {"_id": gcn_id},
            {"$set": {"status": fail_status, "error": str(e), "error_kind": error_kind,
                      "finished_at": datetime.now(timezone.utc)}},
        )
        counts = await bump(mongo.db[config.COLL_BATCH], batch_id,
                            processing=-1, **{fail_status: 1})
        publish_sync({"type": "gcn", "gcn_id": gcn_id, "batch_id": batch_id,
                      "branch": branch, "status": fail_status, "error": str(e)})
        await _rollup(mongo, batch_id, branch, counts)
        return fail_status

    records = records or []
    normalize_extractions(records)

    first = records[0] if records else {}
    # records rỗng KHÔNG có nghĩa là không đọc được file — có thể do không phát
    # hiện được bìa (không nhóm nào) dù ảnh đã render thành công (`images`).
    # Ưu tiên số trang từ ảnh đã render thật để vẫn xem được PDF dù trích xuất lỗi.
    page_count = first.get("page_count") or len(images) or doc.get("page_count", 0)
    skip_reason = first.get("skip_reason")
    has_error = any(r.get("error") for r in records if isinstance(r, dict))

    if skip_reason:
        status, err = "skip", first.get("error")
    elif has_error:
        status = "error"
        err = next((r.get("error") for r in records if r.get("error")), None)
    elif not records:
        # Không có bìa GCN nào trong hồ sơ — KẾT QUẢ HỢP LỆ, KHÔNG phải lỗi xử lý
        # (ảnh đã render OK, VLM soi xong mà không thấy giấy). Tách riêng "no_gcn"
        # để: (a) không thổi phồng ô "Lỗi"; (b) không bị nút "thử lại lỗi" quét
        # (chỉ nhắm status="error") → khỏi requeue vô tận cái mà chạy lại vẫn trống.
        status, err = "no_gcn", "no_gcn_in_document"
    else:
        status, err = "done", None

    cuts: list[dict] = []
    if status == "done" and config.BUILD_CUTS:
        _t = time.monotonic()
        try:
            cuts = await _build_cuts(gcn_id, batch_id, images, records)
            if timings is not None:
                timings["cuts"] = round(time.monotonic() - _t, 3)
        except DestinationNotConfigured as e:
            # Khác các lỗi cắt-trang cục bộ ở nhánh dưới (1 file cắt hỏng thì bỏ
            # qua, giữ "done" không cuts) — thiếu S3 đích là lỗi HỆ THỐNG, ảnh
            # hưởng MỌI cut của doc này. Đánh dấu "error" rõ ràng thay vì lặng lẽ
            # trả "done" mà không có trang đã cắt nào.
            status, err = "error", str(e)
        except Exception as e:  # noqa: BLE001
            log.warning("build_cuts %s lỗi: %s", gcn_id, e)

    # Vá SPH thiếu tiền tố chữ bằng tên tệp — SỬA TẠI CHỖ, phải chạy TRƯỚC
    # collect_so_phat_hanhs/group_key_of/summarize để mọi thứ suy ra từ records
    # đều thấy giá trị đã vá. Thuần chuỗi, chỉ động khi phần SỐ trùng khớp
    # (xem app/sph_ten_tep.py) nên không thêm rủi ro bịa dữ liệu.
    n_va = va_sph_tu_ten_tep(records, doc.get("filename"))
    if n_va:
        log.info("va_sph_ten_tep %s: %d SPH lấy tiền tố từ tên tệp %r",
                 gcn_id, n_va, doc.get("filename"))

    sph_list = collect_so_phat_hanhs(records)

    # Lỗi trích xuất (timeout VLM, không nhận diện được bìa...) — mặc định
    # "transient", cho phép "retry hàng loạt" thử lại (§Quy mô cực lớn 6).
    error_kind = "transient" if status == "error" else None

    # Gắn Mã MĐSD THẲNG vào từng mục đích trong `records` — SỬA TẠI CHỖ, nên
    # phải chạy TRƯỚC khi dựng `update` (mà "extractions" trỏ vào chính records).
    # Thuần chuỗi, không thêm call model nào vào hot path GPU-bound.
    tt_mdsdd = gan_mdsdd(records)

    update = {
        "status": status, "error": err, "error_kind": error_kind, "extractions": records,
        "page_count": page_count, "skip_reason": skip_reason,
        "group_key": group_key_of(records),
        "extracted_so_phat_hanhs": sph_list,
        "summary": summarize(records),
        "gcn_rows": per_gcn(records, cuts), "cuts": cuts,
        # Chủ cuối suy ngay trong pipeline (REGEX-only — thuần, không thêm call LLM
        # vào hot path GPU-bound). Ca canh_bao (có chuyển nhượng nhưng chưa rõ chủ)
        # để backfill LLM định kỳ quét sau, không chặn ingest.
        "chu_cuoi": _chu_cuoi_records(records),
        "chu_cuoi_version": CHU_CUOI_VERSION,
        "mdsdd_version": MDSDD_VERSION,
        "mdsdd_can_ra_tay": tt_mdsdd["can_ra_tay"],
        "mdsdd_ly_do": tt_mdsdd["ly_do"],
        "finished_at": datetime.now(timezone.utc),
    }
    if timings is not None:
        timings["page_count"] = page_count
        timings["n_groups"] = len(records)
        update["timings"] = timings
    await mongo.update_one(config.COLL_GCN, {"_id": gcn_id}, {"$set": update})

    dup_suspect, dup_candidates = False, []
    if status == "done" and sph_list:
        # Đánh dấu nghi trùng nội dung (PLAN_PHASE2.md §⑧): unique index chống
        # trùng theo KEY, không theo NỘI DUNG — 2 lần scan cùng GCN dưới 2 tên
        # khác nhau vẫn ra 2 doc. Không chặn cứng (2 bản scan có thể khác chất
        # lượng), chỉ cảnh báo để hậu kiểm biết mà xử lý.
        dup_suspect, dup_candidates = await _refresh_dup_group(mongo, gcn_id, sph_list)
        # Đối xứng: MỌI hồ sơ vừa tìm thấy cũng phải tính LẠI TOÀN BỘ danh sách
        # của chính họ (không chỉ cộng thêm 1 mình doc này) — tự chữa lành luôn
        # phần dữ liệu lệch tích tụ trước đây của họ (xem docstring
        # `_refresh_dup_group`).
        for other_id in dup_candidates:
            other = await mongo.db[config.COLL_GCN].find_one(
                {"_id": other_id}, {"extracted_so_phat_hanhs": 1})
            if other:
                await _refresh_dup_group(mongo, other_id, other.get("extracted_so_phat_hanhs") or [])

    counts = await bump(mongo.db[config.COLL_BATCH], batch_id, processing=-1, **{status: 1})
    if status == "error" or not is_large:
        publish_sync({"type": "gcn", "gcn_id": gcn_id, "batch_id": batch_id,
                      "branch": branch, "status": status, "group_key": update["group_key"]})
    await _rollup(mongo, batch_id, branch, counts)
    return status


# Throttle SSE mức lô cho batch LỚN (§Quy mô cực lớn 3) — module-level vì worker
# xử lý nhiều doc/batch cùng lúc trong 1 process; không cần chính xác tuyệt đối
# giữa nhiều worker process, chỉ cần "vài giây" như plan yêu cầu.
_last_batch_sse: dict[str, float] = {}


async def _rollup(mongo: AsyncMongo, batch_id, branch=None, counts: dict | None = None) -> None:
    """Cập nhật `batch.status` từ counter đã duy trì (KHÔNG count_documents —
    §Quy mô cực lớn 4) + publish SSE mức lô, throttle cho batch lớn (§3)."""
    if not batch_id:
        return
    if counts is None:  # phòng hờ (không nên xảy ra — mọi caller đều bump trước)
        b = await mongo.db[config.COLL_BATCH].find_one({"_id": batch_id}, {"counts": 1})
        counts = (b or {}).get("counts") or {}
    pending = counts.get("queued", 0) + counts.get("processing", 0)
    status = "done" if pending == 0 else "processing"
    await mongo.update_one(config.COLL_BATCH, {"_id": batch_id}, {"$set": {"status": status}})

    total = sum(counts.values())
    is_large = total > config.SSE_AGG_FILE_THRESHOLD
    now = time.monotonic()
    last = _last_batch_sse.get(batch_id, 0.0)
    if not is_large or pending == 0 or (now - last) >= config.SSE_MIN_INTERVAL:
        _last_batch_sse[batch_id] = now
        publish_sync({"type": "batch", "batch_id": batch_id, "branch": branch,
                      "status": status, "pending": pending, "counts": counts})
        if pending == 0:
            _last_batch_sse.pop(batch_id, None)  # lô xong — dọn cache, tránh rò rỉ dict
