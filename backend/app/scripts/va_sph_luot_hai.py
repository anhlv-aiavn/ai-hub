"""Vá RIÊNG Số phát hành bằng LƯỢT HAI — không extract lại toàn bộ hồ sơ.

Khác `rerun_sph_endpoint` ở đúng một điểm, và đó là điểm quan trọng nhất:
`process_doc` GHI ĐÈ cả `extractions`. Máy vLLM chết giữa chừng là bản ghi lỗi
thay chỗ dữ liệu cũ ⇒ MẤT sạch SPH đang có (đã dính một lần: ['824812'] → []).
Script này chỉ gọi prompt NHẸ (extract_gcn_only) rồi ghép mỗi Số phát hành vào
bản ghi cũ. Không có đường nào làm dữ liệu tệ đi:

  - lỗi/không đọc được → KHÔNG ghi gì, doc y nguyên
  - lượt hai trả giá trị sai form → _ghep_sph từ chối, doc y nguyên
  - chỉ ghi khi SPH cũ SAI FORM và SPH mới ĐÚNG FORM

Rẻ hơn chạy lại: 1 call/giấy thay vì detect + extract đầy đủ + lượt hai.
Đắt nhất vẫn là tải PDF + render ảnh, cái đó không tránh được.

KHÔNG đi qua hàng đợi, KHÔNG đổi `status` ⇒ worker chính không thấy gì, chạy
song song thoải mái. Đánh dấu bằng field riêng `sph_l2_at`.

Chạy TRONG CONTAINER:
    # 1) xem quy mô + mẫu, KHÔNG gọi GPU, KHÔNG ghi:
    docker compose exec api python -m app.scripts.va_sph_luot_hai --dry-run

    # 2) chạy thử 500, ghim máy phụ:
    docker compose exec \
        -e VLLM_ENDPOINTS=http://192.168.120.10:30001/v1 \
        -e MAX_VLM_CONCURRENT=8 \
        api python -m app.scripts.va_sph_luot_hai --limit 500 --song-song 8

    # 3) chạy tiếp phần còn lại (bỏ --limit), trong screen
    # 4) làm lại từ đầu cho nhóm đã chạy: --go-co

Ctrl-C: dừng êm sau khi các hồ sơ đang chạy xong.
"""

import argparse
import asyncio
import os
import signal
import time
from collections import Counter
from datetime import datetime, timezone

from app import config, storage
from app.db import gcns
from app.scripts.requeue_sph_thieu_chu import (
    _MONGO_RE,
    _da_hau_kiem,
    _sph_list,
    _thieu_chu,
    SPH_PATH,
)
from app.summary import collect_so_phat_hanhs, group_key_of, per_gcn, summarize
from src.extentions.multimodal.extract_gcn import (
    _entries_sph,
    _ghep_sph,
    _is_valid_so_phat_hanh,
    _sph_luot_mot,
    extract_gcn_only,
)
from src.extentions.multimodal.make import pdf_to_corrected_images

# Render y hệt run_job — ảnh khác kích cỡ là đọc ra kết quả khác.
RENDER_DPI = int(os.getenv("AIHUB_RENDER_DPI", "200"))
RENDER_MAX_SIZE = int(os.getenv("AIHUB_RENDER_MAX_SIZE", "2000"))

# Bật thinking cho lượt hai (đo tay: đọc seri tốt hơn hẳn). Tắt: SPH_L2_THINKING=false
THINKING = os.getenv("SPH_L2_THINKING", "true").strip().lower() == "true"
# Hỏng LIÊN TIẾP ngần này thì dừng — máy vLLM chết thì cày tiếp cũng vô ích.
MAX_LOI_LIEN_TIEP = int(os.getenv("SPH_L2_MAX_LOI", "10"))

_stop = False


def _xin_dung(*_a) -> None:
    global _stop
    if _stop:
        raise KeyboardInterrupt
    _stop = True
    print("\n  … dừng êm, đợi hồ sơ đang chạy xong (Ctrl-C nữa để cắt ngay)", flush=True)


def _can_va(records) -> bool:
    """Có record nào chứa giấy SPH sai form không (chỗ để ghép vào)."""
    for r in records or []:
        res = r.get("result") if isinstance(r, dict) else None
        if not isinstance(res, dict):
            continue
        g = _entries_sph(res)
        if g and not all(_is_valid_so_phat_hanh(x.get("Số phát hành")) for x in g):
            return True
    return False


async def _va_mot_doc(doc: dict, st: Counter) -> bool:
    """Trả True nếu đã GHI (có SPH được vá). Không ghi gì khi lỗi/không cứu được."""
    records = doc.get("extractions") or []
    if not _can_va(records):
        st["khong_co_cho_ghep"] += 1
        return False

    pdf_buf = await storage.get_pdf(doc["s3_key"], doc.get("source_connection_id"))
    loop = asyncio.get_running_loop()
    images = await loop.run_in_executor(
        None, lambda: pdf_to_corrected_images(
            pdf_buf, dpi=RENDER_DPI, max_img_size=RENDER_MAX_SIZE))
    if not images:
        st["render_rong"] += 1
        return False

    dem_tong = 0
    for r in records:
        res = r.get("result") if isinstance(r, dict) else None
        if not isinstance(res, dict):
            continue
        gcn = _entries_sph(res)
        if not gcn or all(_is_valid_so_phat_hanh(g.get("Số phát hành")) for g in gcn):
            continue
        # Đúng những trang của record này — ghép nhầm ảnh là ghép nhầm giấy.
        idx = r.get("page_indices") or []
        imgs = [images[i] for i in idx if isinstance(i, int) and 0 <= i < len(images)]
        if not imgs:
            st["thieu_trang"] += 1
            continue
        nhe = await extract_gcn_only(
            imgs,
            enable_thinking=True if THINKING else None,
            sph=_sph_luot_mot(gcn))
        dem = _ghep_sph(res, nhe.get("Giấy chứng nhận") or [])
        dem_tong += dem["sph"]

    if not dem_tong:
        st["khong_cuu_duoc"] += 1
        # Vẫn đánh dấu đã thử, để lần chạy sau không tốn GPU lại từ đầu.
        await gcns().update_one({"_id": doc["_id"]},
                                {"$set": {"sph_l2_at": datetime.now(timezone.utc)}})
        return False

    # Chỉ những field phái sinh TỪ SPH. `cuts` giữ nguyên (ảnh cắt không đổi).
    cuts = doc.get("cuts") or []
    await gcns().update_one({"_id": doc["_id"]}, {"$set": {
        "extractions": records,
        "extracted_so_phat_hanhs": collect_so_phat_hanhs(records),
        "group_key": group_key_of(records),
        "summary": summarize(records),
        "gcn_rows": per_gcn(records, cuts),
        "sph_l2_at": datetime.now(timezone.utc),
        "sph_l2_va": dem_tong,
    }})
    st["da_va"] += 1
    st["sph_va"] += dem_tong
    return True


async def run(limit, batch_id, song_song, dry_run, ke_ca_da_hau_kiem) -> None:
    global _stop
    q: dict = {"status": "done", "sph_l2_at": {"$exists": False},
               SPH_PATH: {"$regex": _MONGO_RE}}
    if batch_id:
        q["batch_id"] = batch_id

    print("  đang quét kho…", flush=True)
    cur = gcns().find(q, {"extractions": 1, "s3_key": 1, "source_connection_id": 1,
                          "cuts": 1, "review": 1, "filename": 1, "batch_id": 1})
    docs, st = [], Counter()
    async for d in cur:
        st["xet"] += 1
        if not _thieu_chu(d.get("extractions"), False):
            continue
        if _da_hau_kiem(d) and not ke_ca_da_hau_kiem:
            st["bo_da_hau_kiem"] += 1
            continue
        docs.append(d)
        if limit and len(docs) >= limit:
            break

    print(f"  xét {st['xet']:,} · bỏ vì đã hậu kiểm {st['bo_da_hau_kiem']:,} "
          f"· SẼ VÁ {len(docs):,}\n", flush=True)
    for d in docs[:5]:
        print(f"    {str(d.get('filename'))[:44]:<46} {_sph_list(d.get('extractions'))}")
    if dry_run:
        print("\n  DRY-RUN — chưa gọi GPU, chưa ghi gì.")
        return
    if not docs:
        return

    signal.signal(signal.SIGINT, _xin_dung)
    cong = asyncio.Semaphore(song_song)
    loi_lien_tiep = 0
    t0 = time.monotonic()

    async def _mot(d):
        nonlocal loi_lien_tiep
        if _stop:
            return
        async with cong:
            if _stop:
                return
            try:
                await _va_mot_doc(d, st)
                loi_lien_tiep = 0
            except Exception as e:  # noqa: BLE001 - 1 hồ sơ hỏng không giết cả mẻ
                st["loi"] += 1
                loi_lien_tiep += 1
                print(f"  LỖI {str(d.get('filename'))[:40]}: {type(e).__name__}: {e}",
                      flush=True)
                if loi_lien_tiep >= MAX_LOI_LIEN_TIEP:
                    print(f"\n  {loi_lien_tiep} lỗi LIÊN TIẾP — dừng. "
                          f"Kiểm tra máy vLLM rồi chạy lại.", flush=True)
                    _xin_dung()
            st["xong"] += 1
            if st["xong"] % 25 == 0:
                print(f"  … {st['xong']:,}/{len(docs):,} · vá được {st['da_va']:,} "
                      f"({st['sph_va']:,} SPH) · {time.monotonic() - t0:.0f}s", flush=True)

    await asyncio.gather(*(_mot(d) for d in docs))

    n = st["xong"] or 1
    print(f"\n  XONG {st['xong']:,}/{len(docs):,} · {time.monotonic() - t0:.0f}s")
    print(f"    VÁ ĐƯỢC      {st['da_va']:,}  ({st['da_va'] * 100 // n}%) "
          f"— {st['sph_va']:,} Số phát hành")
    print(f"    không cứu được {st['khong_cuu_duoc']:,}")
    for k in ("loi", "render_rong", "thieu_trang", "khong_co_cho_ghep"):
        if st[k]:
            print(f"    {k:<14} {st[k]:,}")
    print("\n  Chạy lại nhóm này từ đầu: --go-co")


async def go_co(batch_id) -> None:
    q = {"sph_l2_at": {"$exists": True}}
    if batch_id:
        q["batch_id"] = batch_id
    res = await gcns().update_many(q, {"$unset": {"sph_l2_at": "", "sph_l2_va": ""}})
    print(f"  đã gỡ cờ trên {res.modified_count:,} hồ sơ")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Vá riêng Số phát hành bằng lượt hai (không extract lại cả hồ sơ).")
    p.add_argument("--limit", type=int, default=None, help="Chỉ xử N hồ sơ.")
    p.add_argument("--batch-id", default=None, help="Giới hạn 1 lô.")
    p.add_argument("--song-song", type=int, default=8, help="Số hồ sơ chạy cùng lúc.")
    p.add_argument("--dry-run", action="store_true", help="Chỉ đếm + in mẫu.")
    p.add_argument("--ke-ca-da-hau-kiem", action="store_true",
                   help="Đụng cả hồ sơ đã hậu kiểm (mặc định BỎ QUA).")
    p.add_argument("--go-co", action="store_true", help="Gỡ cờ sph_l2_at để chạy lại.")
    args = p.parse_args()
    print(f"DB: {config.MONGO_URI}/{config.MONGO_DB} · thinking={THINKING}")
    if args.go_co:
        asyncio.run(go_co(args.batch_id))
        return
    asyncio.run(run(args.limit, args.batch_id, args.song_song, args.dry_run,
                    args.ke_ca_da_hau_kiem))


if __name__ == "__main__":
    main()
