import asyncio
import io
import os
import re

from src.extentions.multimodal.make import pdf_to_corrected_images
from src.extentions.multimodal.prompt import (
    extract_gcn_only_system_prompt,
    extract_system_prompt,
    pdf_extract_gcn_only_prompt,
    pdf_extract_prompt,
)
from src.extentions.multimodal.vlm_client import ENABLE_THINKING, chat_json

# Tắt mặc định: retry-thinking khi Số phát hành sai form. Sau dùng model tốt hơn
# trám vào sẽ có ý nghĩa hơn — bật lại bằng EXTRACT_RETRY_THINKING=true.
EXTRACT_RETRY_THINKING = os.getenv("EXTRACT_RETRY_THINKING", "false").strip().lower() == "true"

# LƯỢT HAI cho Số phát hành: entry nào SPH sai form thì hỏi lại bằng prompt NHẸ
# (extract_gcn_only) + thinking. Đo tay trên 11 hồ sơ: prompt đầy đủ đọc seri kém
# hẳn prompt nhẹ (I 2250 vs S 012250; 845530 vs S 845590; 342398 vs N 342398) —
# ít trường phải lo nên model soi kỹ được góc bìa. Rẻ hơn chạy lại extract đầy đủ.
SPH_LUOT_HAI = os.getenv("SPH_LUOT_HAI", "true").strip().lower() == "true"
SPH_LUOT_HAI_THINKING = os.getenv("SPH_LUOT_HAI_THINKING", "true").strip().lower() == "true"


_PURE_DIGITS_RE = re.compile(r"^\d{10,15}$")
_LETTER_DIGIT_RE = re.compile(r"^([A-Z01]{1,4})\s*([\dOI]+)$")
_DATE_RE = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b")

# Form hợp lệ của Số phát hành (sau normalize):
#   - bản cũ:  1-4 chữ in hoa + 5+ số      (vd "DD 999053", "AA 00827763")
#   - bản mới: thuần số 8-15 chữ số        (vd "0103040010", "010119545602779")
_VALID_SPH_LETTER = re.compile(r"^[A-ZĐ]{1,4} ?\d{5,}$")
_VALID_SPH_DIGITS = re.compile(r"^\d{8,15}$")

# Tiền tố rác: chữ "Số" in trên phôi bị OCR dính vào mã seri
#   "Số"/"Sô"/"So"/"S0"/"S6"/"S9"/"S°"... đứng ngay trước mã (vd "S6AP 471319")
#   Mã seri không bao giờ có chữ số ở phần chữ → "S + ký tự không phải chữ" là rác.
# Có cả "SĐ" (chữ "Số" đọc thành S+Đ, vd "SĐ A 998055"): Đ là chữ cái thật nhưng
# chốt bên dưới bắt phần còn lại phải đúng form seri mới cho cắt, nên seri thật
# hai chữ "SĐ 124668" vẫn được giữ nguyên.
_SPH_JUNK_PREFIX_RE = re.compile(r"^S\s*[ỐỒỔỖỘÔỎÕỌÓÒƠỚOĐ0-9°:.,]\s*")
# Phần còn lại sau khi bỏ tiền tố phải đúng dạng mã seri thì mới chấp nhận cắt
_SPH_CODE_RE = re.compile(r"^([A-ZĐ]{1,4})\s*([\dOI]{5,})$")
# Nhãn "Số" viết bằng CHỮ, dính liền: "SỐ 005324", "SO 254325", "SĐ 124668".
# HẸP hơn _SPH_JUNK_PREFIX_RE có chủ đích: không nhận chữ số và không cho khoảng
# trắng chen giữa — nếu không thì seri một chữ "S 845590" bị ăn mất số 8 đầu.
_SPH_NHAN_CHU_RE = re.compile(r"^S[ỐỒỔỖỘÔỎÕỌÓÒƠỚOĐ]\s*")


def _is_valid_so_phat_hanh(value) -> bool:
    if not isinstance(value, str):
        return False
    s = value.strip().upper()
    if not s:
        return False
    return bool(_VALID_SPH_DIGITS.fullmatch(s) or _VALID_SPH_LETTER.fullmatch(s))


def _bad_sph_count(result: dict) -> int:
    """Số entry có Số phát hành thiếu/sai form. Không có entry nào → coi như 1 (miss)."""
    if not isinstance(result, dict):
        return 1
    entries = result.get("Đăng ký") or []
    if not entries:
        return 1
    bad = 0
    for e in entries:
        gcn = e.get("Giấy chứng nhận") if isinstance(e, dict) else None
        sph = gcn.get("Số phát hành") if isinstance(gcn, dict) else None
        if not _is_valid_so_phat_hanh(sph):
            bad += 1
    return bad


def _normalize_date(value: str) -> str:
    """Trích dd/mm/yyyy đầu tiên từ chuỗi, bỏ phần thừa (nơi cấp, tỉnh,...)."""
    if not isinstance(value, str) or not value.strip():
        return value
    m = _DATE_RE.search(value)
    if not m:
        return value
    d, mo, y = m.groups()
    return f"{int(d):02d}/{int(mo):02d}/{y}"


def _normalize_so_phat_hanh(value: str) -> str:
    """Sửa các nhầm lẫn OCR phổ biến cho 'Số phát hành'.

    - phần chữ:  0 → O, 1 → I  (vd: B0 175403 → BO 175403, D1 536373 → DI 536373)
    - phần số:   O → 0, I → 1
    - tiền tố 'SO' nếu sau đó vẫn là dạng 2 → bỏ (SOAP → AP)
    - tiền tố rác 'Số/S6/S0/SO' dính trước mã seri → bỏ (S6AP 471319 → AP 471319)
    - dạng thuần số 10-15 chữ số → giữ nguyên (chuẩn hoá khoảng trắng)
    """
    if not isinstance(value, str) or not value.strip():
        return value
    s = re.sub(r"\s+", " ", value.strip()).upper()

    # Bỏ tiền tố "Số" bị OCR dính vào, chỉ khi phần còn lại còn ra hồn mã seri
    stripped = _SPH_JUNK_PREFIX_RE.sub("", s, count=1).strip()
    if stripped != s:
        m_code = _SPH_CODE_RE.fullmatch(stripped)
        if m_code:
            # tách hẳn chữ/số để không bị _LETTER_DIGIT_RE nuốt nhầm (S0AK123456)
            s = f"{m_code.group(1)} {m_code.group(2)}"
        elif (_SPH_NHAN_CHU_RE.match(s) and stripped.isdigit()
              and 4 <= len(stripped) <= 15):
            # "SỐ 005324" → "005324": còn lại số trần, VẪN sai form nhưng đã sạch
            # nhãn — luật vá theo tên tệp (app/sph_ten_tep) nối tiếp được để ra
            # "S 005324". Giữ nguyên "SỐ 005324" thì cả hai đường đều tắc.
            # TRẢ LUÔN: đi tiếp thì _LETTER_DIGIT_RE bắt nhầm "0053"+"24" rồi rơi
            # vào nhánh `return value` — mất sạch công cắt nhãn.
            return stripped

    no_space = s.replace(" ", "")
    if _PURE_DIGITS_RE.fullmatch(no_space):
        return no_space

    m = _LETTER_DIGIT_RE.fullmatch(s)
    if not m:
        return value

    letters_raw = m.group(1)
    # Bảo vệ: phần "chữ" phải có ít nhất 1 ký tự là letter thực sự,
    # tránh biến chuỗi toàn số (vd "12 345") thành "II 345".
    if not any(c.isalpha() for c in letters_raw):
        return value

    letters = letters_raw.replace("0", "O").replace("1", "I")
    digits = m.group(2).replace("O", "0").replace("I", "1")

    if letters.startswith("SO") and len(letters) > 2:
        letters = letters[2:]

    if not digits.isdigit() or not letters:
        return value

    return f"{letters} {digits}"


def _normalize_result(result: dict) -> dict:
    if not isinstance(result, dict):
        return result
    for entry in result.get("Đăng ký", []) or []:
        if not isinstance(entry, dict):
            continue

        gcn = entry.get("Giấy chứng nhận")
        if isinstance(gcn, dict):
            if "Số phát hành" in gcn:
                gcn["Số phát hành"] = _normalize_so_phat_hanh(gcn["Số phát hành"])
            if "Ngày cấp" in gcn:
                gcn["Ngày cấp"] = _normalize_date(gcn["Ngày cấp"])

        for chu in entry.get("Chủ sử dụng", []) or []:
            if not isinstance(chu, dict):
                continue
            for k in ("Ngày cấp", "Ngày cấp định danh"):
                if k in chu:
                    chu[k] = _normalize_date(chu[k])

        for thua in entry.get("Thửa đất", []) or []:
            if not isinstance(thua, dict):
                continue
            for mucdich in thua.get("Mục đích sử dụng", []) or []:
                if isinstance(mucdich, dict) and "Ngày hết hạn sử dụng" in mucdich:
                    mucdich["Ngày hết hạn sử dụng"] = _normalize_date(
                        mucdich["Ngày hết hạn sử dụng"]
                    )
    return result


def _entries_sph(result: dict) -> list[dict]:
    """Các dict "Giấy chứng nhận" trong kết quả extract đầy đủ, theo thứ tự."""
    out = []
    for e in (result or {}).get("Đăng ký") or []:
        gcn = e.get("Giấy chứng nhận") if isinstance(e, dict) else None
        if isinstance(gcn, dict):
            out.append(gcn)
    return out


def _ghep_sph(result: dict, items: list) -> int:
    """Ghép SPH từ kết quả prompt NHẸ vào kết quả đầy đủ. THUẦN (test được).

    Chỉ ghi đè khi giá trị CŨ sai form và giá trị MỚI đúng form — lượt hai không
    bao giờ được phép làm hỏng một SPH đang đúng.

    Ghép theo THỨ TỰ khi hai bên cùng số lượng giấy. Lệch số lượng thì chỉ nhận
    trường hợp không thể nhầm: đúng một entry hỏng và đúng một ứng viên hợp lệ.
    Ngoài ra bỏ qua — ghép mò giữa các giấy khác nhau còn tệ hơn để nguyên."""
    gcns = _entries_sph(result)
    if not gcns or not items:
        return 0
    moi = [it.get("Số phát hành") if isinstance(it, dict) else None for it in items]
    hong = [i for i, g in enumerate(gcns) if not _is_valid_so_phat_hanh(g.get("Số phát hành"))]
    if not hong:
        return 0

    cap: list[tuple[int, str]] = []
    if len(moi) == len(gcns):
        cap = [(i, moi[i]) for i in hong]
    else:
        hop_le = [v for v in moi if _is_valid_so_phat_hanh(v)]
        if len(hong) == 1 and len(hop_le) == 1:
            cap = [(hong[0], hop_le[0])]

    n = 0
    for i, v in cap:
        if _is_valid_so_phat_hanh(v):
            gcns[i]["Số phát hành"] = v.strip().upper()
            n += 1
    return n


async def _luot_hai_sph(images_b64: list[str], result: dict) -> int:
    """Hỏi lại Số phát hành bằng prompt NHẸ + thinking cho các entry sai form."""
    if not SPH_LUOT_HAI or _bad_sph_count(result) == 0:
        return 0
    try:
        nhe = await extract_gcn_only(
            images_b64, enable_thinking=True if SPH_LUOT_HAI_THINKING else None)
    except Exception:  # noqa: BLE001 - lượt phụ: hỏng thì giữ nguyên kết quả chính
        return 0
    return _ghep_sph(result, nhe.get("Giấy chứng nhận") or [])


async def _extract_once(images_b64: list[str], enable_thinking: bool | None) -> dict:
    result = await chat_json(
        system_prompt=extract_system_prompt,
        user_text=pdf_extract_prompt,
        images_b64=images_b64,
        enable_thinking=enable_thinking,
    )
    return _normalize_result(result)


async def extract(
    images_b64: list[str],
    enable_thinking: bool | None = None,
) -> dict:
    """Trích xuất thông tin GCN từ list ảnh (đã được detect group lại).

    enable_thinking: None=theo env, True=ép bật chain-of-thought (dùng cho retry).

    Nếu Số phát hành extract ra THIẾU/SAI FORM mà lần đầu chưa bật thinking →
    retry MỘT lần với thinking=True; giữ kết quả nào ít lỗi Số phát hành hơn.
    """
    if not images_b64:
        return {}

    result = await _extract_once(images_b64, enable_thinking)

    used_thinking = ENABLE_THINKING if enable_thinking is None else enable_thinking
    if EXTRACT_RETRY_THINKING and not used_thinking and _bad_sph_count(result) > 0:
        retry = await _extract_once(images_b64, enable_thinking=True)
        if _bad_sph_count(retry) < _bad_sph_count(result):
            result = retry

    # Lượt hai: chỉ chạm vào entry có SPH sai form, các trường khác giữ nguyên.
    await _luot_hai_sph(images_b64, result)
    return result


def _normalize_gcn_only_result(result: dict) -> dict:
    """Normalize + flatten output của extract_gcn_only.

    Schema model trả về (mới):
        {"Đăng kí": [{"Giấy chứng nhận": [{"Số phát hành":..,"Số vào sổ":..,"Ngày cấp":..}, ...]}, ...]}

    Trả về dạng phẳng để downstream dùng dễ:
        {"Giấy chứng nhận": [<tất cả các GCN tìm được>]}
    """
    if not isinstance(result, dict):
        return {"Giấy chứng nhận": []}

    def _norm_item(item: dict) -> dict | None:
        if not isinstance(item, dict):
            return None
        if "Số phát hành" in item:
            item["Số phát hành"] = _normalize_so_phat_hanh(item["Số phát hành"])
        if "Ngày cấp" in item:
            item["Ngày cấp"] = _normalize_date(item["Ngày cấp"])
        return item

    items: list[dict] = []

    # Schema mới: Đăng kí / Đăng ký → list entry → Giấy chứng nhận → list item
    dang_ky_list = result.get("Đăng kí") or result.get("Đăng ký") or []
    for entry in dang_ky_list:
        if not isinstance(entry, dict):
            continue
        for it in entry.get("Giấy chứng nhận", []) or []:
            n = _norm_item(it)
            if n is not None:
                items.append(n)

    # Backward-compat: model trả về schema phẳng cũ.
    if not items and isinstance(result.get("Giấy chứng nhận"), list):
        for it in result["Giấy chứng nhận"]:
            n = _norm_item(it)
            if n is not None:
                items.append(n)

    return {"Giấy chứng nhận": items}


async def extract_gcn_only(
    images_b64: list[str],
    enable_thinking: bool | None = None,
) -> dict:
    """Phiên bản nhẹ: chỉ lấy Số phát hành / Số vào sổ / Ngày cấp.

    Returns: {"Giấy chứng nhận": [{"Số phát hành": "", "Số vào sổ": "", "Ngày cấp": ""}, ...]}
    """
    if not images_b64:
        return {"Giấy chứng nhận": []}

    result = await chat_json(
        system_prompt=extract_gcn_only_system_prompt,
        user_text=pdf_extract_gcn_only_prompt,
        images_b64=images_b64,
        enable_thinking=enable_thinking,
    )
    return _normalize_gcn_only_result(result)


def _smoke() -> None:
    """Test THUẦN cho normalize + ghép lượt hai (không cần VLM/ảnh).

        python -c "from src.extentions.multimodal.extract_gcn import _smoke; _smoke()"
    """
    n = _normalize_so_phat_hanh
    # nhãn "Số" dính liền → bỏ, phần còn lại là mã seri
    assert n("S6AP 471319") == "AP 471319", "KILL [1]"
    assert n("SĐ A 998055") == "A 998055", "KILL [2] SĐ = Số"
    # nhãn "Số" + số trần → bỏ nhãn, để luật tên tệp nối tiếp (app/sph_ten_tep)
    assert n("SỐ 005324") == "005324", "KILL [3]"
    assert n("SO 254325") == "254325", "KILL [4]"
    # SERI MỘT CHỮ: tuyệt đối KHÔNG được ăn mất chữ số đầu
    assert n("S 845590") == "S 845590", "KILL [5]"
    assert n("S 012250") == "S 012250", "KILL [6]"
    assert n("S 216419") == "S 216419", "KILL [7]"
    # hành vi cũ giữ nguyên
    assert n("B0 175403") == "BO 175403", "KILL [8]"
    assert n("D1 536373") == "DI 536373", "KILL [9]"
    assert n("0103040010") == "0103040010", "KILL [10]"
    assert n("12 345") == "12 345", "KILL [11] chuỗi toàn số giữ nguyên"
    assert n("") == "" and n(None) is None, "KILL [12]"

    def res(*sph):
        return {"Đăng ký": [{"Giấy chứng nhận": {"Số phát hành": v}} for v in sph]}

    def lay(r):
        return [e["Giấy chứng nhận"]["Số phát hành"] for e in r["Đăng ký"]]

    # 1 hỏng ↔ 1 ứng viên hợp lệ → vá
    r = res("845530")
    assert _ghep_sph(r, [{"Số phát hành": "S 845590"}]) == 1, "KILL [13]"
    assert lay(r) == ["S 845590"], "KILL [14]"
    # SPH đang ĐÚNG → lượt hai không được phép đụng
    r = res("AP 471319")
    assert _ghep_sph(r, [{"Số phát hành": "XX 999999"}]) == 0, "KILL [15]"
    assert lay(r) == ["AP 471319"], "KILL [16]"
    # ứng viên mới cũng sai form → giữ nguyên
    r = res("111")
    assert _ghep_sph(r, [{"Số phát hành": "SN 53"}]) == 0, "KILL [17]"
    # cùng số lượng → ghép theo thứ tự, chỉ chạm entry hỏng
    r = res("342398", "AP 471319")
    assert _ghep_sph(r, [{"Số phát hành": "N 342398"},
                         {"Số phát hành": "ZZ 111111"}]) == 1, "KILL [18]"
    assert lay(r) == ["N 342398", "AP 471319"], f"KILL [19] {lay(r)}"
    # lệch số lượng + nhiều entry hỏng → BỎ QUA (ghép mò còn tệ hơn để nguyên)
    r = res("111", "222")
    assert _ghep_sph(r, [{"Số phát hành": "N 342398"}]) == 0, "KILL [20]"
    assert lay(r) == ["111", "222"], "KILL [21]"
    # lệch số lượng nhưng KHÔNG THỂ NHẦM: đúng 1 hỏng, đúng 1 ứng viên hợp lệ
    r = res("111", "AP 471319")
    assert _ghep_sph(r, [{"Số phát hành": "N 342398"}, {"Số phát hành": "rác"}]) == 1, "KILL [22]"
    assert lay(r) == ["N 342398", "AP 471319"], "KILL [23]"
    assert _ghep_sph({}, [{"Số phát hành": "N 342398"}]) == 0, "KILL [24]"
    assert _ghep_sph(res("111"), []) == 0, "KILL [25]"

    print("extract_gcn PURE: 25 KILL ✓")


async def _process_pdf(file_path: str):
    with open(file_path, "rb") as f:
        pdf_bytesio = io.BytesIO(f.read())
    loop = asyncio.get_running_loop()
    images = await loop.run_in_executor(None, pdf_to_corrected_images, pdf_bytesio)
    result = await extract_gcn_only(images)
    print(f"\n{'=' * 30}\n{file_path}\n{result}")
    return result


async def main():
    file_paths = [
        "/home/vpdkhn/bags/ai-hub/tmp/tmp_2/00424-GCN-S 132398.pdf",
        "/home/vpdkhn/bags/ai-hub/tmp/tmp_2/00613-GCN-Y 905245-G5Uui9OC.pdf",
        "/home/vpdkhn/bags/ai-hub/tmp/tmp_2/09835-GCN-N 342398.pdf",
        "/home/vpdkhn/bags/ai-hub/tmp/tmp_2/09835-GCN-N 433457 (1).pdf",
        "/home/vpdkhn/bags/ai-hub/tmp/tmp_2/09835-GCN-N 433457.pdf",
        "/home/vpdkhn/bags/ai-hub/tmp/tmp_2/10183-C-S 845590.pdf",
        "/home/vpdkhn/bags/ai-hub/tmp/tmp_2/10210-C-I 612853.pdf",
        "/home/vpdkhn/bags/ai-hub/tmp/tmp_2/10225-C-I 612206.pdf",
        "/home/vpdkhn/bags/ai-hub/tmp/tmp_2/10231-C-P 765650.pdf",
        "/home/vpdkhn/bags/ai-hub/tmp/tmp_2/10231-C-U 053754.pdf",
        "/home/vpdkhn/bags/ai-hub/tmp/tmp_2/10234-C-A 998055 (1).pdf",
        "/home/vpdkhn/bags/ai-hub/tmp/tmp_2/M 649864.pdf",
        "/home/vpdkhn/bags/ai-hub/tmp/tmp_2/S 005324 (1).pdf",
        "/home/vpdkhn/bags/ai-hub/tmp/tmp_2/S 012250.pdf",
        "/home/vpdkhn/bags/ai-hub/tmp/tmp_2/U 459828.pdf"
    ]
    return await asyncio.gather(*(_process_pdf(p) for p in file_paths))


if __name__ == "__main__":
    asyncio.run(main())
