"""Registry LOẠI GIẤY — điểm khai báo duy nhất cho `gcn` / `ddk` / `kqdk` / `pcctt`.

Vì sao là registry chứ không phải `if doc_type == ...` rải trong worker: mọi thứ
phía sau pipeline (prompt, cách gom trang, summary, khóa, hậu xử lý, cột bảng) đều
KHÁC nhau theo loại giấy. Bốn loại thì `if` đã hết cứu. Worker giữ nguyên khung
claim → render → detect → extract → ghi, chỉ tra registry ở các điểm rẽ; thêm loại
thứ năm = thêm một khai báo ở file này.

Bất biến: nhánh "gcn" phải giữ HÀNH VI Y HỆT trước khi có registry (prompt vendor,
normalize vendor, summary cũ, mdsdd/chu_cuoi/dup-group). Pipeline GCN đang chạy
production hàng triệu hồ sơ — refactor này không được đụng vào kết quả của nó.

HAI CHẾ ĐỘ GOM TRANG
--------------------
`gcn`  — một PDF chứa NHIỀU giấy: phân loại biên từng trang (cover/content/other)
         rồi suy nhóm tuyến tính. Mỗi nhóm = một giấy = một lần extract.

`mot_ho_so` (ddk/kqdk/pcctt) — một PDF là MỘT hồ sơ, phần còn lại là tài liệu đính
         kèm. Phân loại từng trang thành bieu_mau/dinh_kem, VỨT trang đính kèm, đưa
         TOÀN BỘ trang biểu mẫu vào MỘT lần extract.

         Vì sao không gom tuyến tính như GCN: khảo sát 90 file mẫu thật cho thấy
         thứ tự trang không đáng tin — `Đơn ĐK/1054768.pdf` xếp Mẫu 15 mặt trước
         (tr.1), 15a (tr.2), 15c (tr.3), CCCD (tr.4-5), rồi Mẫu 15 MẶT SAU ở tr.6.
         Gom theo vị trí sẽ cắt mất mặt sau — đúng phần có "Đề nghị cấp Giấy chứng
         nhận" và danh sách giấy tờ nộp kèm. Gom-tất-cả-trang-biểu-mẫu miễn nhiễm
         với thứ tự.
"""

import os
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app import doc_prompts
from app.summary import collect_so_phat_hanhs, format_page_range, group_key_of, per_gcn, summarize
from app.vn_text import strip_diacritics
from src.extentions.multimodal.detect_gcn import classify_page
from src.extentions.multimodal.extract_gcn import extract as extract_gcn
from src.extentions.multimodal.normalize_dang_ky import normalize_extractions
from src.extentions.multimodal.vlm_client import chat_json

MAC_DINH = "gcn"

# Chế độ gom trang — xem docstring đầu file.
GOM_GCN = "gcn"
GOM_MOT_HO_SO = "mot_ho_so"

_ROLES_GCN = ("cover", "content", "other")
_ROLES_HO_SO = ("bieu_mau", "dinh_kem")

# Ngưỡng "file ngắn thì khỏi gọi VLM phân loại trang". GCN giữ nguyên env cũ để
# không đổi hành vi vận hành đang chạy. Biểu mẫu: 18/30 phiếu mẫu chỉ có ĐÚNG 1
# trang — với chúng, phân loại là phí hoàn toàn (không có gì để lọc).
_DETECT_MIN_GCN = int(os.getenv("DETECT_MIN_PAGES", "5"))
_DETECT_MIN_HO_SO = int(os.getenv("DETECT_MIN_PAGES_HO_SO", "1"))

# Cùng một thứ — SỐ CCCD/CMND — nhưng mỗi biểu mẫu in một tên khác: pcctt/kqdk
# ghi "Số giấy tờ", ddk (Mẫu 15 mục 1b) ghi "Giấy tờ nhân thân". Gom vào một chỗ
# vì đã có lần chuẩn hoá bắt mỗi tên đầu, khiến ddk lọt lưới hoàn toàn.
_TEN_SO_GIAY_TO = ("Số giấy tờ", "Giấy tờ nhân thân")


# ── Chuẩn hoá nhẹ cho biểu mẫu ──────────────────────────────────────────────

_DATE_RE = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b")
# Dạng chữ trên biểu mẫu: "Trung Giã, ngày 10 tháng 8 năm 2026". Ô ngày thường bị
# BỎ TRỐNG ("ngày .... tháng 7 năm 2026") nên phần ngày để optional.
_NGAY_CHU_RE = re.compile(r"(?:ngày\s*)?(\d{1,2})?\s*tháng\s*(\d{1,2})\s*năm\s*(\d{4})",
                          re.IGNORECASE)
_SO_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
# Số CC/CCCD hay bị chấm phân nhóm: "001085.015.315".
_CHI_SO_RE = re.compile(r"[^\d]")

# Một cụm chữ số liền mạch, cho phép dấu chấm phân nhóm ("001085.015.315") nhưng
# KHÔNG cho khoảng trắng — nhờ vậy "033064004050 22112021" tách thành hai cụm
# thay vì dính làm một chuỗi 20 số rồi trượt hết mọi luật.
_CUM_SO_RE = re.compile(r"\d[\d.]*\d")


def _chuan_ngay(v: str) -> str:
    """Về dd/mm/yyyy. Nhận cả dạng số (10/8/2026) lẫn dạng chữ của biểu mẫu
    ("Trung Giã, ngày 10 tháng 8 năm 2026").

    Ngày bị bỏ trống trên giấy → trả "" chứ KHÔNG giữ nguyên câu. Một ô ngày
    không dùng được thì phải hiện ra là trống để hậu kiểm biết mà điền; giữ lại
    nguyên văn "ngày .... tháng 7 năm 2026" chỉ làm cột thống kê báo 'đã điền'
    trong khi thực tế không có dữ liệu."""
    m = _DATE_RE.search(v)
    if m:
        d, mo, y = m.groups()
        return f"{int(d):02d}/{int(mo):02d}/{y}"
    m = _NGAY_CHU_RE.search(v)
    if not m:
        return v
    d, mo, y = m.groups()
    if not d:
        return ""
    return f"{int(d):02d}/{int(mo):02d}/{y}"


def _chuan_so_giay_to(v: str) -> str:
    """Bỏ dấu phân nhóm khỏi CMND/CCCD ("001085.015.315" → "001085015315"), CHỈ
    khi phần số còn lại đúng 9 hoặc 12 chữ số. Không khớp → giữ nguyên văn, để
    người hậu kiểm thấy đúng thứ model đọc được.

    Ô "Giấy tờ nhân thân" của Mẫu 15 là một dòng kẻ trống nên người dân hay viết
    cả câu: "CCCD số 033064004050 cấp ngày 22/11/2021". Gặp vậy thì nhặt lấy cụm
    9/12 số trong câu. Ngày cấp bị bỏ là CÓ CHỦ Ý: schema không có ô cho nó, và
    với người sử dụng chung thì Mẫu 15a đã có cột "Ngày cấp" riêng.

    Chỉ nhặt khi tìm được ĐÚNG một cụm hợp lệ — hai cụm trở lên thì không đoán
    được cụm nào là của người đứng đơn, giữ nguyên văn cho người hậu kiểm."""
    so = _CHI_SO_RE.sub("", v)
    if len(so) in (9, 12):
        return so
    hop_le = {c for m in _CUM_SO_RE.finditer(v)
              if len(c := _CHI_SO_RE.sub("", m.group(0))) in (9, 12)}
    return hop_le.pop() if len(hop_le) == 1 else v


def _chuan_dien_tich(v: str) -> str:
    """"120,5 m²" → "120.5". Không parse được thì giữ NGUYÊN VĂN — thà để người
    hậu kiểm nhìn thấy chuỗi gốc còn hơn nuốt mất giá trị."""
    m = _SO_RE.search(v.replace(",", "."))
    return m.group(0) if m else v


def _normalize_bieu_mau(node: Any) -> Any:
    """Chuẩn hoá đệ quy theo TÊN TRƯỜNG: trường tên chứa "Ngày" → dd/mm/yyyy, chứa
    "Diện tích" → số. Không đụng trường khác.

    Cố tình mỏng: đây là dữ liệu người dân VIẾT TAY, sửa nhiều là bịa. Việc nặng
    (tách chủ, suy chủ cuối, gán mã MĐSD) chỉ có nghĩa với GCN."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if isinstance(v, str) and v.strip():
                if "Ngày" in k:
                    v = _chuan_ngay(v.strip())
                elif "Diện tích" in k:
                    v = _chuan_dien_tich(v.strip())
                elif any(t in k for t in _TEN_SO_GIAY_TO):
                    v = _chuan_so_giay_to(v.strip())
            out[k] = _normalize_bieu_mau(v)
        return out
    if isinstance(node, list):
        return [_normalize_bieu_mau(x) for x in node]
    return node


def _normalize_inplace(records: list[dict] | None) -> None:
    for rec in records or []:
        if isinstance(rec, dict) and isinstance(rec.get("result"), dict):
            rec["result"] = _normalize_bieu_mau(rec["result"])


# ── Đọc dữ liệu ra khỏi kết quả biểu mẫu ────────────────────────────────────

def _than(records: list[dict] | None, key: str) -> dict:
    """Thân hồ sơ (object dưới `key`) của bản ghi đầu tiên đọc được. Một file =
    một hồ sơ nên chỉ có một; duyệt hết cho chắc thay vì giả định records[0]."""
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        res = rec.get("result")
        if isinstance(res, dict) and isinstance(res.get(key), dict):
            return res[key]
    return {}


def _lay(node: Any, *duong_dan: str) -> str:
    """Giá trị theo đường dẫn lồng nhau; đứt ở bất kỳ nấc nào → ""."""
    cur = node
    for k in duong_dan:
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(k)
    if cur is None or isinstance(cur, (dict, list)):
        return ""
    return str(cur)


def _ten_nguoi(than: dict, *nguon: tuple) -> list[str]:
    """Gom tên người từ nhiều vị trí: ("Người sử dụng đất", "Họ và tên") cho object
    đơn lẻ, hoặc ("Người sử dụng chung",) cho mảng có khóa "Tên"/"Họ và tên"."""
    ra: list[str] = []
    for vt in nguon:
        node = than.get(vt[0])
        if len(vt) > 1:
            v = _lay(node, *vt[1:]) if isinstance(node, dict) else ""
            if v:
                ra.append(v)
        elif isinstance(node, list):
            for e in node:
                if isinstance(e, dict):
                    v = str(e.get("Họ và tên") or e.get("Tên") or "")
                    if v:
                        ra.append(v)
    # Bỏ trùng, GIỮ THỨ TỰ (người đứng đơn phải ở đầu — cột bảng hiển thị tên đầu).
    return list(dict.fromkeys(ra))


# ── Khai báo một loại giấy ───────────────────────────────────────────────────

@dataclass(frozen=True)
class LoaiGiay:
    ma: str
    nhan: str
    che_do_gom: str
    detect_min_pages: int
    roles: tuple
    classify: Callable[[str], Awaitable[str]]
    extract: Callable[[list[str]], Awaitable[dict]]
    normalize: Callable[[list[dict]], None]
    # summarize/rows nhận thêm `khoa` (lấy từ source key — xem `khoa_tu_nguon`);
    # nhánh GCN bỏ qua tham số này.
    summarize: Callable[..., dict]
    rows: Callable[..., list[dict]]
    group_key: Callable[..., str | None]
    collect_keys: Callable[..., list[str]]
    cut_stem: Callable[[dict], str | None]
    build_cuts: bool = True
    dedup_trang: bool = False
    # Hậu xử lý CHỈ có nghĩa với GCN (mã MĐSD, chủ cuối, vá SPH theo tên tệp, đánh
    # dấu nghi trùng theo Số phát hành). Bật cho biểu mẫu = sinh field rác.
    hau_xu_ly_gcn: bool = False
    phien_ban: str = "v1"


def _sampling() -> dict:
    """Tham số sinh cho nhánh BIỂU MẪU (ddk/kqdk/pcctt). Đọc env ở mỗi lần gọi
    chứ không cache lúc import: mỗi lần `docker compose exec -e VAR=... ` là một
    tiến trình mới, nên dò tham số được ngay mà khỏi build lại image.

    KHÔNG đụng nhánh GCN — nhánh đó gọi extract_gcn của src/extentions và đang
    chạy sản xuất, đổi sampling là đổi kết quả hàng triệu bản ghi.

    max_tokens là cái chặn treo, không phải cái chỉnh chất lượng: model lặp vòng
    sẽ sinh tới khi hết ngữ cảnh, giữ chỗ GPU hàng phút và kéo tụt cả hàng đợi
    (đã gặp: 5 request cùng lảm nhảm ở 460 tok/s, cả lô 90 tệp đứng im). 0 = không
    đặt trần, để server quyết như cũ."""
    return {
        "temperature": float(os.getenv("BIEU_MAU_TEMPERATURE", "0")),
        "repetition_penalty": float(os.getenv("BIEU_MAU_REPETITION_PENALTY", "1.0")),
        "max_tokens": int(os.getenv("BIEU_MAU_MAX_TOKENS", "0")) or None,
    }


def _classify_ho_so(prompt_sys: str, prompt_user: str):
    """Phân loại một trang: thuộc biểu mẫu hay là tài liệu đính kèm."""
    async def _fn(image_b64: str) -> str:
        if not image_b64:
            return "bieu_mau"
        d = await chat_json(system_prompt=prompt_sys, user_text=prompt_user,
                            images_b64=[image_b64], **_sampling())
        role = (d.get("role") or "").strip().lower() if isinstance(d, dict) else ""
        # Nhãn lạ → giữ trang. Vứt nhầm một trang biểu mẫu là mất dữ liệu im lặng;
        # giữ nhầm một trang đính kèm chỉ tốn thêm chút ngữ cảnh.
        return role if role in _ROLES_HO_SO else "bieu_mau"
    return _fn


def _extract_ho_so(prompt_sys: str, prompt_user: str, key: str):
    """Extract MỘT hồ sơ từ toàn bộ trang biểu mẫu → {key: {...}}."""
    async def _fn(images_b64: list[str]) -> dict:
        if not images_b64:
            return {}
        d = await chat_json(system_prompt=prompt_sys, user_text=prompt_user,
                            images_b64=images_b64, **_sampling())
        if not isinstance(d, dict):
            return {key: {}}
        than = d.get(key)
        if isinstance(than, dict):
            return {key: than}
        if isinstance(than, list):   # model trả mảng dù prompt yêu cầu object
            dau = next((x for x in than if isinstance(x, dict)), {})
            return {key: dau}
        # Model bỏ luôn khóa bọc ngoài, trả thẳng nội dung → bọc lại cho đồng nhất
        # thay vì bắt cả tầng dưới phòng thủ hai dạng. {"raw": ...} = JSON hỏng.
        noi_dung = {k: v for k, v in d.items() if k != "raw"}
        return {key: noi_dung}
    return _fn


def _summary_ho_so(than: dict, khoa: str, ngay: str, nguon_ten: tuple) -> dict:
    """Cột tóm tắt cho một hồ sơ biểu mẫu.

    DÙNG LẠI ĐÚNG TÊN KHÓA của summary GCN (so_phat_hanh / chu_su_dung / ngay_cap /
    to_ban_do / so_thua) để bảng trích xuất, tìm kiếm và export CSV hiện có chạy
    được ngay, chưa phải sửa frontend. `khoa_chinh` là phần bổ sung để sau này tách
    cột riêng theo loại mà không phải chạy lại kho.

    `so_phat_hanh` = KHÓA NGUỒN (đường dẫn/tên tệp) chứ không phải số hiệu đọc từ
    giấy: ddk và pcctt không in số hiệu nào, còn thứ đọc được thì toàn chữ viết tay
    nên không đáng làm khóa."""
    thua = than.get("Thửa đất") if isinstance(than.get("Thửa đất"), dict) else {}
    to_ban_do = [v for v in [_lay(thua, "Tờ bản đồ số")] if v]
    so_thua = [v for v in [_lay(thua, "Thửa đất số")] if v]
    for t in than.get("Danh sách thửa") or []:      # phụ lục Mẫu 15b
        if isinstance(t, dict):
            if t.get("Tờ bản đồ số"):
                to_ban_do.append(str(t["Tờ bản đồ số"]))
            if t.get("Thửa đất số"):
                so_thua.append(str(t["Thửa đất số"]))
    return {
        "so_phat_hanh": khoa,
        "khoa_chinh": khoa,
        "so_vao_so": _lay(than, "Thông tin văn bản", "Số vào sổ ĐKĐĐ"),
        "ngay_cap": ngay,
        "chu_su_dung": _ten_nguoi(than, *nguon_ten),
        "to_ban_do": to_ban_do,
        "so_thua": so_thua,
    }


def _rows_ho_so(records, cuts, khoa: str, key: str, ngay_fn, nguon_ten) -> list[dict]:
    """1 hàng / 1 hồ sơ — gương của `summary.per_gcn` cho chế độ mot_ho_so."""
    cutmap = {c.get("index"): c for c in (cuts or []) if isinstance(c, dict)}
    out: list[dict] = []
    for ri, rec in enumerate(records or []):
        if not isinstance(rec, dict):
            continue
        res = rec.get("result")
        than = res.get(key) if isinstance(res, dict) else None
        if not isinstance(than, dict) or not than:
            continue
        cut = cutmap.get(ri)
        page_idx = rec.get("page_indices") or []
        row = _summary_ho_so(than, khoa, ngay_fn(than), nguon_ten)
        # Bản bỏ dấu cho tìm kiếm không phân biệt dấu — cùng hợp đồng với
        # summary.per_gcn (routes/gcn.py tìm theo gcn_rows.chu_su_dung_norm).
        row["chu_su_dung_norm"] = [strip_diacritics(c) for c in row["chu_su_dung"]]
        row["cut_index"] = ri if cut else None
        row["page_count"] = (cut or {}).get("page_count") or len(page_idx)
        row["page_indices"] = page_idx
        row["page_range"] = format_page_range(page_idx)
        out.append(row)
    return out


def _lam_loai_ho_so(ma: str, nhan: str, key: str, prompts: dict, ngay_fn,
                    nguon_ten: tuple) -> LoaiGiay:
    return LoaiGiay(
        ma=ma,
        nhan=nhan,
        che_do_gom=GOM_MOT_HO_SO,
        detect_min_pages=_DETECT_MIN_HO_SO,
        roles=_ROLES_HO_SO,
        classify=_classify_ho_so(prompts["classify_sys"], prompts["classify_user"]),
        extract=_extract_ho_so(prompts["extract_sys"], prompts["extract_user"], key),
        normalize=_normalize_inplace,
        summarize=lambda recs, khoa="": {
            **_summary_ho_so(_than(recs, key), khoa, ngay_fn(_than(recs, key)), nguon_ten),
            "gcn_count": 1 if _than(recs, key) else 0,
            "so_phat_hanhs": [khoa] if khoa else [],
        },
        rows=lambda recs, cuts, khoa="": _rows_ho_so(recs, cuts, khoa, key, ngay_fn, nguon_ten),
        # Khóa/gom nhóm KHÔNG suy từ nội dung — lấy thẳng khóa nguồn.
        group_key=lambda recs, khoa="": khoa or None,
        collect_keys=lambda recs, khoa="": [khoa] if khoa else [],
        cut_stem=lambda rec: None,
        dedup_trang=True,
        phien_ban="v1",
    )


# ── gcn — GIỮ NGUYÊN hành vi cũ ─────────────────────────────────────────────

GCN = LoaiGiay(
    ma="gcn",
    nhan="Giấy chứng nhận",
    che_do_gom=GOM_GCN,
    detect_min_pages=_DETECT_MIN_GCN,
    roles=_ROLES_GCN,
    classify=classify_page,
    extract=extract_gcn,
    normalize=normalize_extractions,
    summarize=lambda recs, khoa="": summarize(recs),
    rows=lambda recs, cuts, khoa="": per_gcn(recs, cuts),
    group_key=lambda recs, khoa="": group_key_of(recs),
    collect_keys=lambda recs, khoa="": collect_so_phat_hanhs(recs),
    cut_stem=lambda rec: None,      # run_job dùng _entry_sph cũ cho GCN
    hau_xu_ly_gcn=True,
)


# ── ddk · kqdk · pcctt ──────────────────────────────────────────────────────

DDK = _lam_loai_ho_so(
    "ddk", "Đơn đăng ký", "Đơn đăng ký",
    {"classify_sys": doc_prompts.ddk_classify_system_prompt,
     "classify_user": doc_prompts.ddk_classify_user_prompt,
     "extract_sys": doc_prompts.ddk_extract_system_prompt,
     "extract_user": doc_prompts.ddk_extract_user_prompt},
    ngay_fn=lambda than: _lay(than, "Thông tin đơn", "Ngày ký"),
    nguon_ten=(("Người sử dụng đất", "Họ và tên"), ("Người sử dụng chung",)),
)

KQDK = _lam_loai_ho_so(
    "kqdk", "Giấy xác nhận đăng ký", "Giấy xác nhận",
    {"classify_sys": doc_prompts.kqdk_classify_system_prompt,
     "classify_user": doc_prompts.kqdk_classify_user_prompt,
     "extract_sys": doc_prompts.kqdk_extract_system_prompt,
     "extract_user": doc_prompts.kqdk_extract_user_prompt},
    ngay_fn=lambda than: _lay(than, "Thông tin văn bản", "Ngày ký"),
    nguon_ten=(("Người sử dụng đất",),),
)

PCCTT = _lam_loai_ho_so(
    "pcctt", "Phiếu thu thập thông tin", "Phiếu thu thập",
    {"classify_sys": doc_prompts.pcctt_classify_system_prompt,
     "classify_user": doc_prompts.pcctt_classify_user_prompt,
     "extract_sys": doc_prompts.pcctt_extract_system_prompt,
     "extract_user": doc_prompts.pcctt_extract_user_prompt},
    ngay_fn=lambda than: _lay(than, "Ngày lập"),
    nguon_ten=(("Người sử dụng đất", "Họ và tên người đại diện"),),
)


# ── Khóa nghiệp vụ = KHÓA NGUỒN ─────────────────────────────────────────────

def khoa_tu_nguon(doc: dict) -> str:
    """Khóa của một hồ sơ biểu mẫu = đường dẫn tệp nguồn, bỏ đuôi .pdf.

    Chốt cùng khách: ddk/kqdk/pcctt KHÔNG có số hiệu in trên giấy (kqdk có số văn
    bản nhưng vẫn là chữ đọc bằng OCR). Lấy khóa từ tên/đường dẫn tệp thì chính xác
    tuyệt đối, miễn nhiễm với chất lượng OCR chữ viết tay — vốn là điểm yếu lớn
    nhất của ba loại này.

    - Doc import từ kho nguồn: dùng NGUYÊN đường dẫn (prefix MinIO) — nó mang cả
      ngữ cảnh thư mục, và là thứ duy nhất nối ngược được về hệ thống gốc.
    - Doc upload trực tiếp: `s3_key` chỉ là "{batch}/{uuid}.pdf" (vô nghĩa) nên
      dùng tên tệp người dùng tải lên.
    """
    if doc.get("source_connection_id"):
        khoa = str(doc.get("s3_key") or "")
    else:
        khoa = str(doc.get("filename") or "")
    return re.sub(r"\.pdf$", "", khoa.strip(), flags=re.IGNORECASE)


# ── Tra cứu ─────────────────────────────────────────────────────────────────

DOC_TYPES: dict[str, LoaiGiay] = {d.ma: d for d in (GCN, DDK, KQDK, PCCTT)}
MA_HOP_LE = tuple(DOC_TYPES)


def get(ma: str | None) -> LoaiGiay:
    """Tra loại giấy; rỗng/lạ → GCN (tương thích ngược với mọi doc đã có trong
    Mongo từ trước khi có field `doc_type`)."""
    return DOC_TYPES.get((ma or "").strip().lower() or MAC_DINH, GCN)


def hop_le(ma: str | None) -> str:
    """Validate cho tầng API. Rỗng → mặc định; lạ → ValueError."""
    v = (ma or "").strip().lower() or MAC_DINH
    if v not in DOC_TYPES:
        raise ValueError(
            f"doc_type không hợp lệ: {ma!r}. Chọn một trong: {', '.join(MA_HOP_LE)}")
    return v


# ── PURE smoke (`docker compose exec api python -m app.doc_types`) ──────────

def _smoke() -> None:
    import asyncio

    # ── Tra cứu + tương thích ngược ─────────────────────────────────────────
    assert get(None).ma == "gcn", "KILL [1] doc cũ không field → GCN"
    assert get("  KQDK ").ma == "kqdk", "KILL [2] chuẩn hoá hoa/thường + trắng"
    assert get("lung tung").ma == "gcn", "KILL [3] giá trị lạ → GCN, không nổ worker"
    assert hop_le(None) == "gcn", "KILL [4] API rỗng → mặc định"
    try:
        hop_le("xyz")
        raise AssertionError("KILL [5] hop_le phải từ chối giá trị lạ")
    except ValueError as e:
        assert "xyz" in str(e) and "pcctt" in str(e), f"KILL [6] message nêu lựa chọn: {e}"

    # ── GCN trỏ ĐÚNG hàm cũ — chốt chặn refactor không đổi hành vi ──────────
    assert GCN.che_do_gom == GOM_GCN and GCN.detect_min_pages == _DETECT_MIN_GCN, \
        "KILL [7] GCN đổi chế độ/ngưỡng detect"
    assert GCN.hau_xu_ly_gcn and not GCN.dedup_trang, "KILL [8] GCN bật hậu xử lý, KHÔNG dedup"
    assert GCN.summarize([], "kệ") == summarize([]), "KILL [9] GCN bỏ qua tham số khóa"
    assert GCN.normalize is normalize_extractions, "KILL [10] GCN đổi normalize"

    for dt in (DDK, KQDK, PCCTT):
        assert dt.che_do_gom == GOM_MOT_HO_SO, f"KILL [11] {dt.ma} phải là mot_ho_so"
        assert dt.dedup_trang and not dt.hau_xu_ly_gcn, f"KILL [12] {dt.ma} cờ sai"

    # ── Khóa nguồn ──────────────────────────────────────────────────────────
    assert khoa_tu_nguon({"source_connection_id": "s1", "s3_key": "2026/Don DK/1054768.pdf",
                          "filename": "1054768.pdf"}) == "2026/Don DK/1054768", \
        "KILL [13] import kho nguồn → giữ nguyên prefix"
    assert khoa_tu_nguon({"s3_key": "batch-uuid/gcn-uuid.pdf",
                          "filename": "1319332.PDF"}) == "1319332", \
        "KILL [14] upload → tên tệp người dùng, không phải s3_key uuid"
    assert khoa_tu_nguon({}) == "", "KILL [15] thiếu cả hai → rỗng, không nổ"

    # ── ddk: Mẫu 15 + phụ lục 15a/15b ───────────────────────────────────────
    recs = [{"page_indices": [0, 5], "result": {"Đơn đăng ký": {
        "Thông tin đơn": {"Ngày ký": "31.7.2026"},
        "Người sử dụng đất": {"Họ và tên": "HOÀNG THĂNG LONG",
                              "Giấy tờ nhân thân": "CCCD số 001 085 015 315"},
        "Thửa đất": {"Thửa đất số": "39", "Tờ bản đồ số": "393", "Diện tích": "74,375 m²"},
        "Người sử dụng chung": [{"Tên": "HOÀNG THĂNG LONG"}, {"Tên": "NGUYỄN THỊ HUYỀN"}],
        "Danh sách thửa": [{"Thửa đất số": "40", "Tờ bản đồ số": "393"}],
    }}}]
    DDK.normalize(recs)
    than = recs[0]["result"]["Đơn đăng ký"]
    assert than["Thông tin đơn"]["Ngày ký"] == "31/07/2026", f"KILL [16] ngày: {than}"
    assert than["Thửa đất"]["Diện tích"] == "74.375", f"KILL [17] diện tích: {than}"
    # ddk gọi số CCCD là "Giấy tờ nhân thân" chứ không phải "Số giấy tờ" — đã có
    # lần chuẩn hoá chỉ bắt tên sau, ddk lọt lưới 100%, smoke E2E báo 40% đúng dạng.
    assert than["Người sử dụng đất"]["Giấy tờ nhân thân"] == "001085015315", \
        f"KILL [17a] tên trường CCCD riêng của ddk vẫn phải qua chuẩn hoá: {than}"

    # Ngày dạng chữ trên biểu mẫu + ô ngày bỏ trống (gặp thật ở pcctt 1218602).
    assert _chuan_ngay("Trung Giã, ngày 10 tháng 8 năm 2026") == "10/08/2026", "KILL [16a] ngày chữ"
    assert _chuan_ngay("30 tháng 7 năm 2026") == "30/07/2026", "KILL [16b] thiếu chữ 'ngày'"
    assert _chuan_ngay("Trung Giã, ngày .... tháng 7 năm 2026") == "", \
        "KILL [16c] ô ngày bỏ trống → rỗng, KHÔNG giữ nguyên câu"
    assert _chuan_ngay("bạ nhạ") == "bạ nhạ", "KILL [16d] không nhận ra → giữ nguyên văn"
    assert _chuan_so_giay_to("001085.015.315") == "001085015315", "KILL [16e] bỏ chấm CCCD"
    assert _chuan_so_giay_to("019084001782") == "019084001782", "KILL [16f] đã sạch thì giữ"
    assert _chuan_so_giay_to("12345") == "12345", "KILL [16g] không đủ 9/12 số → giữ nguyên"
    # Ô Mẫu 15 mục 1b là dòng kẻ trống → người dân viết cả câu (gặp thật ở 1134303).
    assert _chuan_so_giay_to("CCCD số 033064004050 cấp ngày 22/11/2021") == "033064004050", \
        "KILL [16h] nhặt CCCD ra khỏi câu"
    assert _chuan_so_giay_to("CMND 019084001 do CA Hà Nội cấp") == "019084001", \
        "KILL [16i] nhặt CMND 9 số ra khỏi câu"
    assert _chuan_so_giay_to("033064004050 và 001085015315") == "033064004050 và 001085015315", \
        "KILL [16j] hai cụm hợp lệ → không đoán, giữ nguyên văn"
    assert _chuan_so_giay_to("MST 0100109106 của công ty") == "MST 0100109106 của công ty", \
        "KILL [16k] pháp nhân 10 số không phải CCCD → giữ nguyên"

    KHOA = "2026/Don DK/1054768"
    s = DDK.summarize(recs, KHOA)
    assert s["khoa_chinh"] == s["so_phat_hanh"] == KHOA, f"KILL [18] khóa = khóa nguồn: {s}"
    assert s["ngay_cap"] == "31/07/2026", f"KILL [19] {s}"
    assert s["chu_su_dung"] == ["HOÀNG THĂNG LONG", "NGUYỄN THỊ HUYỀN"], \
        f"KILL [20] gộp người đứng đơn + Mẫu 15a, bỏ trùng, giữ thứ tự: {s}"
    assert s["so_thua"] == ["39", "40"] and s["to_ban_do"] == ["393", "393"], \
        f"KILL [21] thửa chính + phụ lục 15b: {s}"
    assert DDK.group_key(recs, KHOA) == KHOA, "KILL [22] group_key = khóa nguồn"
    assert DDK.group_key(recs, "") is None, "KILL [23] không có khóa → None, không bịa"

    rows = DDK.rows(recs, [{"index": 0, "page_count": 2}], KHOA)
    assert len(rows) == 1 and rows[0]["page_range"] == "1, 6", \
        f"KILL [24] trang rời rạc (mặt sau nằm cuối file): {rows}"
    assert rows[0]["chu_su_dung_norm"][0] == "hoang thang long", f"KILL [25] {rows}"

    # ── kqdk: nhiều người + số vào sổ ───────────────────────────────────────
    rk = [{"page_indices": [0, 1], "result": {"Giấy xác nhận": {
        "Thông tin văn bản": {"Số văn bản": "108/GXN-VPĐKĐĐTT", "Ngày ký": "3/1/2018",
                               "Số vào sổ ĐKĐĐ": "852.2018"},
        "Người sử dụng đất": [{"Họ và tên": "TRẦN KIM DUY"}, {"Họ và tên": "ĐỖ THỊ THU THUỶ"}],
        "Thửa đất": {"Thửa đất số": "17(2)", "Tờ bản đồ số": "46"},
    }}}]
    KQDK.normalize(rk)
    sk = KQDK.summarize(rk, "253008")
    assert sk["chu_su_dung"] == ["TRẦN KIM DUY", "ĐỖ THỊ THU THUỶ"], f"KILL [26] {sk}"
    assert sk["so_vao_so"] == "852.2018", f"KILL [27] số vào sổ ĐKĐĐ: {sk}"
    assert sk["ngay_cap"] == "03/01/2018", f"KILL [28] {sk}"

    # ── pcctt: một trang, viết tay ──────────────────────────────────────────
    rp = [{"page_indices": [0], "result": {"Phiếu thu thập": {
        "Người sử dụng đất": {"Họ và tên người đại diện": "Nguyễn Duy Phúc"},
        "Thửa đất": {"Thửa đất số": "23", "Tờ bản đồ số": "166", "Diện tích": "4512,8"},
        "Ngày lập": "10/8/2026"}}}]
    PCCTT.normalize(rp)
    sp = PCCTT.summarize(rp, "1319332")
    assert sp["chu_su_dung"] == ["Nguyễn Duy Phúc"], f"KILL [29] {sp}"
    assert sp["ngay_cap"] == "10/08/2026" and sp["so_thua"] == ["23"], f"KILL [30] {sp}"

    # ── Đầu vào rác KHÔNG được làm nổ pipeline ──────────────────────────────
    for dt in (DDK, KQDK, PCCTT):
        assert dt.summarize([], "k")["gcn_count"] == 0, f"KILL [31] {dt.ma} records rỗng"
        assert dt.summarize(None, "k")["chu_su_dung"] == [], f"KILL [32] {dt.ma} None"
        assert dt.rows([{"result": None}], None, "k") == [], f"KILL [33] {dt.ma} result None"
        assert dt.rows([{"result": {"Phiếu thu thập": "chuỗi"}}], None, "k") == [], \
            f"KILL [34] {dt.ma} thân không phải dict"
        dt.normalize([{"result": {"x": ["không", "phải", "dict"]}}])   # KILL [35] không raise

    # ── Tham số sinh: mặc định phải y hệt hành vi trước khi có env ──────────
    for k in ("BIEU_MAU_TEMPERATURE", "BIEU_MAU_REPETITION_PENALTY", "BIEU_MAU_MAX_TOKENS"):
        os.environ.pop(k, None)
    assert _sampling() == {"temperature": 0.0, "repetition_penalty": 1.0, "max_tokens": None}, \
        f"KILL [42a] không đặt env → tất định, không trần: {_sampling()}"
    os.environ["BIEU_MAU_TEMPERATURE"] = "0.2"
    os.environ["BIEU_MAU_REPETITION_PENALTY"] = "1.05"
    os.environ["BIEU_MAU_MAX_TOKENS"] = "3000"
    assert _sampling() == {"temperature": 0.2, "repetition_penalty": 1.05, "max_tokens": 3000}, \
        f"KILL [42b] env phải vào thẳng: {_sampling()}"
    os.environ["BIEU_MAU_MAX_TOKENS"] = "0"
    assert _sampling()["max_tokens"] is None, "KILL [42c] 0 = không đặt trần, KHÔNG phải trần 0"
    for k in ("BIEU_MAU_TEMPERATURE", "BIEU_MAU_REPETITION_PENALTY", "BIEU_MAU_MAX_TOKENS"):
        os.environ.pop(k, None)

    # ── Bọc kết quả model trả sai hình dạng ─────────────────────────────────
    global chat_json
    that = chat_json
    fn = _extract_ho_so("s", "u", "Phiếu thu thập")
    try:
        async def _mang(**k):
            return {"Phiếu thu thập": [{"Ngày lập": "1/1/2026"}, {"Ngày lập": "2/2/2026"}]}
        chat_json = _mang
        assert asyncio.run(fn(["img"])) == {"Phiếu thu thập": {"Ngày lập": "1/1/2026"}}, \
            "KILL [36] model trả mảng → lấy phần tử đầu (một file một hồ sơ)"

        async def _tran(**k):
            return {"Thửa đất": {"Thửa đất số": "9"}}
        chat_json = _tran
        assert asyncio.run(fn(["img"])) == {"Phiếu thu thập": {"Thửa đất": {"Thửa đất số": "9"}}}, \
            "KILL [37] model bỏ khóa bọc ngoài → bọc lại"

        os.environ["BIEU_MAU_REPETITION_PENALTY"] = "1.07"
        os.environ["BIEU_MAU_MAX_TOKENS"] = "2500"
        nhan = {}
        async def _bat(**k):
            nhan.update(k)
            return {"Phiếu thu thập": {}}
        chat_json = _bat
        asyncio.run(fn(["img"]))
        assert nhan.get("repetition_penalty") == 1.07 and nhan.get("max_tokens") == 2500, \
            f"KILL [37a] sampling phải tới tận chat_json, không dừng ở _sampling(): {nhan}"
        nhan.clear()
        asyncio.run(_classify_ho_so("s", "u")("img"))
        assert nhan.get("max_tokens") == 2500, f"KILL [37b] classify cũng phải có trần: {nhan}"
        for k in ("BIEU_MAU_REPETITION_PENALTY", "BIEU_MAU_MAX_TOKENS"):
            os.environ.pop(k, None)

        async def _hong(**k):
            return {"raw": "JSON hỏng"}
        chat_json = _hong
        assert asyncio.run(fn(["img"])) == {"Phiếu thu thập": {}}, "KILL [38] JSON hỏng → thân rỗng"
        assert asyncio.run(fn([])) == {}, "KILL [39] không ảnh → {}"

        # classify: nhãn lạ / lỗi → GIỮ trang (vứt nhầm là mất dữ liệu im lặng)
        cls = _classify_ho_so("s", "u")
        async def _la(**k):
            return {"role": "không biết"}
        chat_json = _la
        assert asyncio.run(cls("img")) == "bieu_mau", "KILL [40] nhãn lạ → giữ trang"
        async def _kem(**k):
            return {"role": "DINH_KEM"}
        chat_json = _kem
        assert asyncio.run(cls("img")) == "dinh_kem", "KILL [41] nhãn hoa/thường"
        assert asyncio.run(cls("")) == "bieu_mau", "KILL [42] ảnh rỗng → giữ"
    finally:
        chat_json = that

    print("doc_types PURE: 52 KILL ✓")


if __name__ == "__main__":
    _smoke()
