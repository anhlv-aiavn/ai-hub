import asyncio
import io
import re

from src.extentions.multimodal.make import pdf_to_corrected_images
from src.extentions.multimodal.prompt import (
    extract_gcn_only_system_prompt,
    extract_system_prompt,
    pdf_extract_gcn_only_prompt,
    pdf_extract_prompt,
)
from src.extentions.multimodal.vlm_client import chat_json


_PURE_DIGITS_RE = re.compile(r"^\d{10,15}$")
_LETTER_DIGIT_RE = re.compile(r"^([A-Z01]{1,4})\s*([\dOI]+)$")
_DATE_RE = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b")


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
    - dạng thuần số 10-15 chữ số → giữ nguyên (chuẩn hoá khoảng trắng)
    """
    if not isinstance(value, str) or not value.strip():
        return value
    s = re.sub(r"\s+", " ", value.strip()).upper()

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


async def extract(
    images_b64: list[str],
    enable_thinking: bool | None = None,
) -> dict:
    """Trích xuất thông tin GCN từ list ảnh (đã được detect group lại).

    enable_thinking: None=theo env, True=ép bật chain-of-thought (dùng cho retry).
    """
    if not images_b64:
        return {}

    result = await chat_json(
        system_prompt=extract_system_prompt,
        user_text=pdf_extract_prompt,
        images_b64=images_b64,
        enable_thinking=enable_thinking,
    )
    return _normalize_result(result)


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


async def _process_pdf(file_path: str):
    with open(file_path, "rb") as f:
        pdf_bytesio = io.BytesIO(f.read())
    loop = asyncio.get_running_loop()
    images = await loop.run_in_executor(None, pdf_to_corrected_images, pdf_bytesio)
    result = await extract(images)
    print(f"\n{'=' * 30}\n{file_path}\n{result}")
    return result


async def main():
    file_paths = [
        "/home/vpdk02/nlp/bags/extract_gcn/tests/output/0_AA 00827763-GCN.pdf",
    ]
    return await asyncio.gather(*(_process_pdf(p) for p in file_paths))


if __name__ == "__main__":
    asyncio.run(main())
