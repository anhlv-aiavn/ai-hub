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

    Người dân hay bỏ trống mỗi Ô NGÀY mà vẫn điền tháng/năm ("ngày .... tháng 7
    năm 2026"). Trả "07/2026" — mất ngày chứ không mất cả tháng lẫn năm, vì
    tháng/năm là thông tin thật có trên giấy và dùng được cho tra cứu.

    Vẫn KHÔNG giữ nguyên văn cả câu: chuỗi "Trung Giã, ngày .... tháng 7 năm
    2026" làm cột thống kê báo 'đã điền' trong khi bên nhận không parse được gì.
    Không đọc ra nổi tháng/năm thì mới trả nguyên văn cho người hậu kiểm nhìn."""
    m = _DATE_RE.search(v)
    if m:
        d, mo, y = m.groups()
        return f"{int(d):02d}/{int(mo):02d}/{y}"
    m = _NGAY_CHU_RE.search(v)
    if not m:
        return v
    d, mo, y = m.groups()
    if not d:
        return f"{int(mo):02d}/{y}"
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
    # Khóa bọc ngoài của `result` ("Phiếu thu thập", "Đơn đăng ký"…). GCN dùng
    # "Đăng ký" và là mảng, nên để rỗng — chỗ nào cần thì kiểm hau_xu_ly_gcn.
    khoa_than: str = ""
    # False = TẠM TẮT: không cho chọn khi tạo lô, không hiện trên UI, không nằm
    # trong --bo-ba. Vẫn ở lại registry để doc CŨ đã gắn mã này đọc đúng prompt
    # và summary của nó — gỡ hẳn khỏi registry thì get() sẽ rơi về GCN và bóc
    # lại/hiển thị sai toàn bộ hồ sơ cũ.
    bat: bool = True
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


# Nhiệt độ cho LƯỢT HAI khi lượt đầu trả về không phải JSON. 0 = tắt hẳn lượt hai.
#
# Vì sao phải đổi nhiệt độ chứ không gọi lại y nguyên: lượt đầu chạy temperature=0
# nên vòng lặp là TẤT ĐỊNH — gọi lại cùng tham số thì lặp y hệt, chỉ tốn thêm 30
# giây. Nhiễu nhỏ là đủ để model rẽ sang nhánh khác. Đánh đổi: bản ghi cứu được
# bằng lượt hai không còn tái lập được từng chữ, nhưng có dữ liệu đọc được vẫn
# hơn mất trắng cả hồ sơ.
def _luot_hai() -> dict:
    """Tham số LƯỢT HAI, chỉ chạy khi lượt đầu trả về không phải JSON.

    Phạt lặp mạnh là con dao hai lưỡi: đo trên 90 hồ sơ thì repetition_penalty
    1.25 phá được vòng lặp của 1319332 (5 giây, 11/11) nhưng làm hỏng chữ số ở
    hồ sơ khác — số căn cước cùng xã dùng chung tiền tố, model né token vừa sinh
    nên đổi chữ số, thậm chí xuất ra "001..." thay cho dãy số. Đặt ở lượt hai thì
    được cái lợi mà không phải trả cái giá: chỉ 1 hồ sơ đã hỏng sẵn phải chịu,
    89 hồ sơ còn lại không đụng tới.

    Nhiệt độ cũng phải khác 0: lượt đầu tất định nên gọi lại y nguyên sẽ lặp y
    hệt. Riêng 1319332 thì nhiệt độ 0.3 KHÔNG đủ (vẫn lặp, 27666 ký tự) — nên
    lượt hai đổi cả hai thứ cùng lúc. Đặt nhiệt độ = 0 là tắt hẳn lượt hai.
    """
    return {
        "temperature": float(os.getenv("BIEU_MAU_RETRY_TEMPERATURE", "0.3")),
        "repetition_penalty": float(os.getenv("BIEU_MAU_RETRY_REPETITION_PENALTY", "1.25")),
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
        if isinstance(d, dict) and "raw" in d and _luot_hai()["temperature"] > 0:
            d = await chat_json(system_prompt=prompt_sys, user_text=prompt_user,
                                images_b64=images_b64, **{**_sampling(), **_luot_hai()})
        if not isinstance(d, dict):
            return {key: {}}
        than = d.get(key)
        if isinstance(than, dict):
            return {key: than}
        if isinstance(than, list):   # model trả mảng dù prompt yêu cầu object
            dau = next((x for x in than if isinstance(x, dict)), {})
            return {key: dau}
        # vlm_client trả {"raw": ...} khi KHÔNG parse nổi JSON. Trước đây chỗ này
        # nuốt thành thân rỗng, nên một hồ sơ mất trắng hiện ra y hệt một tờ phiếu
        # dân bỏ trống: status=done, error=None, 0 ô. Đã đo nhầm vì nó (1319332,
        # 1329445 chạy 30s rồi báo "0/11"). Ném lỗi để run_job ghi vào `error` —
        # dữ liệu hỏng phải nhìn thấy được, thà đỏ còn hơn im lặng.
        if "raw" in d:
            raw = d.get("raw")
            n = len(raw) if isinstance(raw, str) else 0
            raise ValueError(
                f"model không trả JSON hợp lệ ({n} ký tự) kể cả sau lượt hai — thường "
                f"do sinh lặp vòng rồi bị cắt ở BIEU_MAU_MAX_TOKENS")
        # Model bỏ luôn khóa bọc ngoài, trả thẳng nội dung → bọc lại cho đồng nhất
        # thay vì bắt cả tầng dưới phòng thủ hai dạng.
        return {key: dict(d)}
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


# ── Mapping biểu mẫu → hình dạng GCN ────────────────────────────────────────
#
# Bên dùng API chỉ đọc các trường của GCN, nên ddk/pcctt cũng phải trả ra khối
# "Đăng ký" đúng hình dạng ấy. Ba điều quyết định thiết kế:
#
# 1. TÍNH LÚC ĐỌC, KHÔNG LƯU. Lưu thêm một bản vào Mongo thì sau khi hậu kiểm
#    sửa trường gốc, bản map giữ nguyên giá trị cũ — bên nhận đọc phải dữ liệu
#    trước khi sửa mà không hay biết. Tính lúc đọc thì nguồn sự thật luôn là
#    khối gốc, và override của hậu kiểm tự động phản ánh sang.
# 2. KHÔNG BỊA. Số phát hành/Số vào sổ/Mã vạch/Ngày cấp của GIẤY CHỨNG NHẬN và
#    Biến động đều không tồn tại trên đơn/phiếu — để rỗng. Riêng "Số phát hành"
#    dùng KHÓA NGUỒN, thống nhất với `_summary_ho_so` (đã chốt với khách: mấy
#    loại này không in số hiệu nào nên lấy tên tệp/prefix làm khóa).
# 3. MAP THEO Ý NGHĨA, KHÔNG THEO TÊN. Ba cái bẫy đã gặp thật khi đối chiếu dữ
#    liệu: pcctt "Ngày cấp" là ngày cấp CCCD (→ "Ngày cấp định danh", KHÔNG phải
#    "Ngày cấp" của sổ); "Ngày lập" là ngày điền phiếu, không phải ngày cấp; ddk
#    "Sở hữu chung" là DIỆN TÍCH ("52") còn GCN "Hình thức sở hữu" là CHỮ
#    ("Chung"). Map theo tên là hỏng cả ba.

_GCN_TRONG = {"Số phát hành": "", "Mã vạch": "", "Số vào sổ": "", "Ngày cấp": ""}


def _md(loai: str, dien_tich: str = "", chung: str = "", thoi_han: str = "",
        nguon: str = "") -> list[dict]:
    """Một dòng "Mục đích sử dụng" theo khung GCN. Rỗng hết thì trả [] — mảng
    một phần tử toàn rỗng làm bảng phẳng sinh ra hàng trắng."""
    if not any((loai, dien_tich, nguon)):
        return []
    return [{"Loại mục đích": loai, "Diện tích": dien_tich, "Sử dụng chung": chung,
             "Thời hạn sử dụng": thoi_han, "Ngày hết hạn sử dụng": "",
             "Nguồn gốc chi tiết": nguon}]


def _thua_gcn(t: dict, so="Thửa đất số", to="Tờ bản đồ số") -> dict:
    return {
        "Số thứ tự thửa": _lay(t, so),
        "Số hiệu tờ bản đồ": _lay(t, to),
        "Diện tích": _lay(t, "Diện tích"),
        "Địa chỉ": _lay(t, "Địa chỉ"),
        "Mục đích sử dụng": _md(_lay(t, "Mục đích sử dụng"), _lay(t, "Diện tích"),
                                _lay(t, "Sử dụng chung"),
                                _lay(t, "Thời hạn đề nghị") or _lay(t, "Thời hạn sử dụng"),
                                _lay(t, "Nguồn gốc sử dụng")),
    }


def _chu_gcn(ten: str, so_giay_to: str = "", nam_sinh: str = "", loai_gt: str = "",
             ngay_cap: str = "", noi_cap: str = "", dia_chi: str = "") -> dict:
    return {
        "Loại đối tượng": "", "Tên chủ": ten, "Năm sinh": nam_sinh, "Giới tính": "",
        "Loại giấy tờ": loai_gt, "Số giấy tờ": so_giay_to,
        # CHÚ Ý: "Ngày cấp định danh" là ngày cấp CCCD/CMND, KHÁC "Ngày cấp" của
        # giấy chứng nhận ở khối trên. Đây là chỗ dễ map nhầm nhất.
        "Ngày cấp định danh": ngay_cap, "Nơi cấp": noi_cap, "Địa chỉ": dia_chi,
    }


def _sang_gcn_pcctt(than: dict, khoa: str) -> list[dict]:
    nsd = than.get("Người sử dụng đất") if isinstance(than.get("Người sử dụng đất"), dict) else {}
    thua = than.get("Thửa đất") if isinstance(than.get("Thửa đất"), dict) else {}
    chu = _chu_gcn(_lay(nsd, "Họ và tên người đại diện"), _lay(nsd, "Số giấy tờ"),
                   ngay_cap=_lay(nsd, "Ngày cấp"), noi_cap=_lay(nsd, "Nơi cấp"),
                   dia_chi=_lay(nsd, "Địa chỉ"))
    return [{
        "Giấy chứng nhận": {**_GCN_TRONG, "Số phát hành": khoa},
        "Chủ sử dụng": [chu] if chu["Tên chủ"] else [],
        "Thửa đất": [_thua_gcn(thua)] if thua else [],
        "Thông tin nhà ở": [],
        "Biến động": [],
    }]


def _sang_gcn_ddk(than: dict, khoa: str) -> list[dict]:
    nsd = than.get("Người sử dụng đất") if isinstance(than.get("Người sử dụng đất"), dict) else {}
    thua = than.get("Thửa đất") if isinstance(than.get("Thửa đất"), dict) else {}
    nha = than.get("Nhà ở, công trình xây dựng")
    nha = nha if isinstance(nha, dict) else {}

    chu = []
    dung = _lay(nsd, "Họ và tên")
    if dung:
        chu.append(_chu_gcn(dung, _lay(nsd, "Giấy tờ nhân thân"), dia_chi=_lay(nsd, "Địa chỉ")))
    # Mẫu 15a thường lặp lại chính người đứng đơn ở dòng 1 — bỏ trùng theo tên
    # đã bỏ dấu, giữ thứ tự, giống hệt cách `_ten_nguoi` gộp cho cột tóm tắt.
    da_co = {strip_diacritics(dung).lower()} if dung else set()
    for n in than.get("Người sử dụng chung") or []:
        if not isinstance(n, dict):
            continue
        ten = _lay(n, "Tên")
        if not ten or strip_diacritics(ten).lower() in da_co:
            continue
        da_co.add(strip_diacritics(ten).lower())
        chu.append(_chu_gcn(ten, _lay(n, "Số giấy tờ"), _lay(n, "Năm sinh"),
                            _lay(n, "Loại giấy tờ"), _lay(n, "Ngày cấp"),
                            _lay(n, "Cơ quan cấp"), _lay(n, "Địa chỉ")))

    ds_thua = [_thua_gcn(thua)] if thua else []
    for t in than.get("Danh sách thửa") or []:       # phụ lục Mẫu 15b
        if isinstance(t, dict) and any(t.values()):
            ds_thua.append(_thua_gcn(t))

    def _ts(loai, dt_xd, dt_san, hinh_thuc, so_tang, thoi_han):
        return {"Loại tài sản gắn liền với đất": loai, "Khu nhà chung cư, nhà hỗn hợp": "",
                "Địa chỉ": "", "Nhà chung cư": "", "Số căn hộ": "",
                "Diện tích xây dựng": dt_xd, "Diện tích sàn": dt_san,
                "Hình thức sở hữu": hinh_thuc, "Thời hạn sở hữu": thoi_han,
                "Cấp hạng": "", "Kết cấu": "", "Số tầng": so_tang}

    # Mẫu 15c KHI CÓ thì THAY mục 3, không cộng thêm. Biểu mẫu quy định vậy: có
    # nhiều tài sản thì mục 3 chỉ ghi thông tin chung và tổng diện tích, còn liệt
    # kê từng cái ở 15c. Cộng cả hai là đếm đúp — đo trên 30 đơn thật: 20 đơn có
    # CẢ hai (cùng một tài sản), 10 đơn chỉ có mục 3, KHÔNG đơn nào chỉ có 15c.
    # (Thửa đất thì ngược lại: 0/30 đơn có cả mục 2 lẫn 15b, nên nối thẳng.)
    ds_15c = [t for t in (than.get("Danh sách tài sản") or [])
              if isinstance(t, dict) and _lay(t, "Loại")]
    nha_o = []
    if ds_15c:
        for t in ds_15c:
            nha_o.append(_ts(_lay(t, "Loại"), _lay(t, "Diện tích xây dựng"),
                             _lay(t, "Diện tích sàn"), _lay(t, "Hình thức sở hữu"),
                             _lay(t, "Số tầng"), _lay(t, "Thời hạn sở hữu")))
    elif _lay(nha, "Loại"):
        # "Sở hữu chung"/"Sở hữu riêng" của ddk là DIỆN TÍCH (m²), không phải chữ
        # chung/riêng — suy ra hình thức từ ô NÀO được điền, chứ không chép giá trị.
        hinh_thuc = ("Chung" if _lay(nha, "Sở hữu chung")
                     else "Riêng" if _lay(nha, "Sở hữu riêng") else "")
        nha_o.append(_ts(_lay(nha, "Loại"), _lay(nha, "Diện tích xây dựng"),
                         _lay(nha, "Diện tích sàn"), hinh_thuc, _lay(nha, "Số tầng"),
                         _lay(nha, "Thời hạn sở hữu đến")))

    return [{
        "Giấy chứng nhận": {**_GCN_TRONG, "Số phát hành": khoa},
        "Chủ sử dụng": chu,
        "Thửa đất": ds_thua,
        "Thông tin nhà ở": nha_o,
        "Biến động": [],
    }]


def _sang_gcn_kqdk(than: dict, khoa: str) -> list[dict]:
    thua = than.get("Thửa đất") if isinstance(than.get("Thửa đất"), dict) else {}
    tt = than.get("Thông tin văn bản") if isinstance(than.get("Thông tin văn bản"), dict) else {}
    chu = []
    for n in than.get("Người sử dụng đất") or []:
        if isinstance(n, dict) and _lay(n, "Họ và tên"):
            chu.append(_chu_gcn(_lay(n, "Họ và tên"), _lay(n, "Số giấy tờ"),
                                dia_chi=_lay(n, "Địa chỉ")))
    return [{
        # kqdk là văn bản xác nhận ĐÃ đăng ký nên CÓ số vào sổ ĐKĐĐ thật — trường
        # duy nhất trong ba loại biểu mẫu điền được vào khối "Giấy chứng nhận".
        "Giấy chứng nhận": {**_GCN_TRONG, "Số phát hành": khoa,
                            "Số vào sổ": _lay(tt, "Số vào sổ ĐKĐĐ")},
        "Chủ sử dụng": chu,
        "Thửa đất": [_thua_gcn(thua)] if thua else [],
        "Thông tin nhà ở": [],
        "Biến động": [],
    }]


_SANG_GCN = {"pcctt": _sang_gcn_pcctt, "ddk": _sang_gcn_ddk, "kqdk": _sang_gcn_kqdk}


def dang_ky_view(result: Any, ma: str, khoa: str) -> list:
    """`result` của một bản ghi → khối "Đăng ký" theo hình dạng GCN.

    Trả về nguyên khối "Đăng ký" nếu đã có (doc GCN thật). Với biểu mẫu thì map
    từ khối gốc. Không đọc được gì → [] chứ không phải một phần tử rỗng: một
    phần tử toàn rỗng sẽ đẻ ra hàng trắng trong CSV, nhìn y như hồ sơ bóc hỏng."""
    if not isinstance(result, dict):
        return []
    if isinstance(result.get("Đăng ký"), list):
        return result["Đăng ký"]
    dt = DOC_TYPES.get(ma)
    fn = _SANG_GCN.get(ma)
    if not dt or not fn:
        return []
    than = result.get(dt.khoa_than)
    return fn(than, khoa) if isinstance(than, dict) and than else []


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
                    nguon_ten: tuple, bat: bool = True) -> LoaiGiay:
    return LoaiGiay(
        ma=ma,
        bat=bat,
        khoa_than=key,
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
    bat=False,   # khách tạm dừng loại này (2026-08-18) — xem cờ `bat`
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
# Chỉ loại đang BẬT mới được chọn. Doc cũ mang mã đã tắt vẫn tra được qua get().
MA_HOP_LE = tuple(m for m, d in DOC_TYPES.items() if d.bat)


def get(ma: str | None) -> LoaiGiay:
    """Tra loại giấy; rỗng/lạ → GCN (tương thích ngược với mọi doc đã có trong
    Mongo từ trước khi có field `doc_type`)."""
    return DOC_TYPES.get((ma or "").strip().lower() or MAC_DINH, GCN)


def hop_le(ma: str | None, tao_moi: bool = True) -> str:
    """Validate cho tầng API. Rỗng → mặc định; lạ → ValueError.

    tao_moi=False cho đường ĐỌC (lọc, thống kê, xuất). Loại đang tắt vẫn phải
    đọc được: tắt là ngừng nhận hồ sơ MỚI, không phải giấu dữ liệu đã bóc. Chặn
    cả đường đọc thì `GET /v1/gcn?doc_type=kqdk` trả 400 và người dùng mất luôn
    lối vào những hồ sơ họ đã xử lý."""
    v = (ma or "").strip().lower() or MAC_DINH
    if tao_moi and v in DOC_TYPES and not DOC_TYPES[v].bat:
        raise ValueError(f"doc_type {v!r} đang tạm tắt, không nhận hồ sơ mới. "
                         f"Chọn một trong: {', '.join(MA_HOP_LE)}")
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

    # Loại TẮT: không chọn được nữa, nhưng doc cũ mang mã đó vẫn phải tra đúng —
    # gỡ khỏi registry thì get() rơi về GCN và hiển thị sai toàn bộ hồ sơ cũ.
    assert "kqdk" not in MA_HOP_LE, "KILL [4a] loại đã tắt không được nằm trong danh sách chọn"
    assert get("kqdk") is KQDK, "KILL [4b] doc CŨ mang mã đã tắt vẫn phải tra ra đúng loại"
    try:
        hop_le("kqdk")
    except ValueError as e:
        assert "tạm tắt" in str(e), f"KILL [4c] báo rõ là tắt, khác với mã sai: {e}"
    else:
        raise AssertionError("KILL [4d] API phải từ chối loại đang tắt khi TẠO MỚI")
    assert hop_le("kqdk", tao_moi=False) == "kqdk", \
        "KILL [4d2] đường ĐỌC vẫn phải cho lọc loại đã tắt — tắt là ngừng nhận mới, không giấu dữ liệu cũ"
    try:
        hop_le("xyz", tao_moi=False)
    except ValueError:
        pass
    else:
        raise AssertionError("KILL [4d3] mã LẠ thì đường đọc vẫn phải từ chối")
    assert all(DOC_TYPES[m].bat for m in MA_HOP_LE), "KILL [4e] danh sách chọn chỉ gồm loại bật"
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
    assert _chuan_ngay("Trung Giã, ngày .... tháng 7 năm 2026") == "07/2026", \
        "KILL [16c] bỏ trống mỗi ngày → giữ tháng/năm, KHÔNG nuốt cả câu"
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

    # ── Map biểu mẫu → hình dạng GCN ────────────────────────────────────────
    KH = "2026/Phieu CCTT/576827"
    pc = {"Phiếu thu thập": {
        "Người sử dụng đất": {"Họ và tên người đại diện": "Nguyễn Văn Luyến",
                              "Số giấy tờ": "001074010045", "Ngày cấp": "13/06/2022",
                              "Nơi cấp": "Cục Cảnh sát", "Địa chỉ": "Thôn Kim Ninh"},
        "Thửa đất": {"Thửa đất số": "299", "Tờ bản đồ số": "386", "Diện tích": "194.0",
                     "Địa chỉ": "Thôn Kim Ninh", "Mục đích sử dụng": "Đất ao",
                     "Nguồn gốc sử dụng": "Nhận chuyển nhượng"},
        "Ngày lập": "14/08/2026"}}
    e = dang_ky_view(pc, "pcctt", KH)[0]
    assert e["Giấy chứng nhận"]["Số phát hành"] == KH, \
        "KILL [43] Số phát hành = khóa nguồn, thống nhất với _summary_ho_so"
    assert e["Giấy chứng nhận"]["Ngày cấp"] == "", (
        "KILL [44] 'Ngày cấp' của GCN phải TRỐNG — giấy chứng nhận chưa tồn tại. "
        "Nhét ngày cấp CCCD hay ngày lập phiếu vào đây là bịa ngày cấp sổ")
    chu = e["Chủ sử dụng"][0]
    assert chu["Ngày cấp định danh"] == "13/06/2022" and chu["Số giấy tờ"] == "001074010045", \
        f"KILL [45] ngày cấp CCCD về đúng 'Ngày cấp định danh': {chu}"
    t = e["Thửa đất"][0]
    assert (t["Số thứ tự thửa"], t["Số hiệu tờ bản đồ"], t["Diện tích"]) == ("299", "386", "194.0"), \
        f"KILL [46] thửa map đúng cột: {t}"
    assert t["Mục đích sử dụng"][0]["Nguồn gốc chi tiết"] == "Nhận chuyển nhượng", \
        "KILL [47] nguồn gốc → Nguồn gốc chi tiết (mdsdd gán mã từ Loại mục đích)"
    assert e["Thông tin nhà ở"] == [] and e["Biến động"] == [], \
        "KILL [48] pcctt không có tài sản/biến động → mảng RỖNG, không phải phần tử rỗng"

    dd = {"Đơn đăng ký": {
        "Người sử dụng đất": {"Họ và tên": "HOÀNG THĂNG LONG",
                              "Giấy tờ nhân thân": "001085015315", "Địa chỉ": "Số 5"},
        "Thửa đất": {"Thửa đất số": "39", "Tờ bản đồ số": "393", "Diện tích": "74.375"},
        "Nhà ở, công trình xây dựng": {"Loại": "Căn hộ", "Diện tích sàn": "52",
                                       "Sở hữu chung": "52", "Sở hữu riêng": ""},
        "Người sử dụng chung": [{"Tên": "Hoàng Thăng Long"},        # trùng người đứng đơn
                                {"Tên": "NGUYỄN THỊ HUYỀN", "Số giấy tờ": "001185002222",
                                 "Năm sinh": "1985", "Loại giấy tờ": "CCCD",
                                 "Cơ quan cấp": "CA Hà Nội"}],
        "Danh sách thửa": [{"Thửa đất số": "40", "Tờ bản đồ số": "393"}]}}
    e = dang_ky_view(dd, "ddk", "k")[0]
    assert [c["Tên chủ"] for c in e["Chủ sử dụng"]] == ["HOÀNG THĂNG LONG", "NGUYỄN THỊ HUYỀN"], \
        f"KILL [49] Mẫu 15a lặp lại người đứng đơn → bỏ trùng (không phân biệt dấu/hoa): {e['Chủ sử dụng']}"
    assert e["Chủ sử dụng"][1]["Nơi cấp"] == "CA Hà Nội", "KILL [50] 'Cơ quan cấp' 15a → 'Nơi cấp'"
    assert [t["Số thứ tự thửa"] for t in e["Thửa đất"]] == ["39", "40"], \
        f"KILL [51] mục 2 + Mẫu 15b nối lại (đo thật: 0/30 đơn có cả hai nên nối là an toàn)"
    assert e["Thông tin nhà ở"][0]["Hình thức sở hữu"] == "Chung", (
        "KILL [52] ddk 'Sở hữu chung' là DIỆN TÍCH (m²) còn GCN 'Hình thức sở hữu' là CHỮ — "
        "suy ra từ ô nào được điền, TUYỆT ĐỐI không chép giá trị sang")

    dd2 = {"Đơn đăng ký": {**dd["Đơn đăng ký"],
                           "Danh sách tài sản": [{"Loại": "Chung cư", "Diện tích sàn": "52",
                                                  "Hình thức sở hữu": "Chung"}]}}
    ts = dang_ky_view(dd2, "ddk", "k")[0]["Thông tin nhà ở"]
    assert [x["Loại tài sản gắn liền với đất"] for x in ts] == ["Chung cư"], (
        "KILL [53] có Mẫu 15c thì 15c THAY mục 3, không cộng — đo thật 20/30 đơn có cả hai "
        f"mà là CÙNG một tài sản: {ts}")

    kq = {"Giấy xác nhận": {"Thông tin văn bản": {"Số vào sổ ĐKĐĐ": "852.2018"},
                            "Người sử dụng đất": [{"Họ và tên": "TRẦN KIM DUY"}],
                            "Thửa đất": {"Thửa đất số": "7"}}}
    e = dang_ky_view(kq, "kqdk", "k")[0]
    assert e["Giấy chứng nhận"]["Số vào sổ"] == "852.2018", \
        "KILL [54] kqdk CÓ số vào sổ ĐKĐĐ thật — trường duy nhất điền được vào khối GCN"

    assert dang_ky_view({"Đăng ký": [{"x": 1}]}, "gcn", "k") == [{"x": 1}], \
        "KILL [55] doc GCN thật thì trả nguyên khối, không map lại"
    for xau in ({}, {"Phiếu thu thập": {}}, {"Phiếu thu thập": "chuỗi"}, None, []):
        assert dang_ky_view(xau, "pcctt", "k") == [], \
            f"KILL [56] không đọc được → [] (một phần tử rỗng sẽ đẻ hàng trắng trong CSV): {xau}"

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

        # Lượt hai: lượt đầu lặp vòng ở temperature=0 thì gọi lại y nguyên cũng
        # lặp y hệt, nên lượt hai BẮT BUỘC phải đổi nhiệt độ mới có nghĩa.
        goi = []
        async def _hong_roi_duoc(**k):
            goi.append((k.get("temperature"), k.get("repetition_penalty")))
            if len(goi) == 1:
                return {"raw": "JSON hỏng vì bị cắt giữa chừng"}
            return {"Phiếu thu thập": {"Ngày lập": "10/08/2026"}}
        chat_json = _hong_roi_duoc
        assert asyncio.run(fn(["img"])) == {"Phiếu thu thập": {"Ngày lập": "10/08/2026"}}, \
            "KILL [38] JSON hỏng → thử lại lượt hai, cứu được thì dùng"
        assert goi == [(0.0, 1.0), (0.3, 1.25)], \
            f"KILL [38a] lượt hai phải đổi CẢ nhiệt độ LẪN phạt lặp: {goi}"
        # Phạt lặp mạnh chỉ được ở lượt hai — áp cho lượt đầu là hỏng chữ số cả lô.
        assert _sampling()["repetition_penalty"] == 1.0, \
            "KILL [38a2] lượt ĐẦU phải giữ phạt lặp trung tính"

        async def _hong(**k):
            return {"raw": "JSON hỏng vì bị cắt giữa chừng"}
        chat_json = _hong
        try:
            asyncio.run(fn(["img"]))
        except ValueError as e:
            assert "MAX_TOKENS" in str(e) and "lượt hai" in str(e), \
                f"KILL [38b] hỏng cả hai lượt → nêu rõ đã thử lại: {e}"
        else:
            raise AssertionError("KILL [38c] hỏng cả hai lượt phải NÉM LỖI, không trả thân rỗng")

        os.environ["BIEU_MAU_RETRY_TEMPERATURE"] = "0"
        dem = []
        async def _dem(**k):
            dem.append(1)
            return {"raw": "hỏng"}
        chat_json = _dem
        try:
            asyncio.run(fn(["img"]))
        except ValueError:
            pass
        assert len(dem) == 1, f"KILL [38d] đặt 0 phải TẮT hẳn lượt hai: gọi {len(dem)} lần"
        os.environ.pop("BIEU_MAU_RETRY_TEMPERATURE", None)
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

    print("doc_types PURE: 77 KILL ✓")


if __name__ == "__main__":
    _smoke()
