"""Fallback LLM TEXT-ONLY cho 'chủ cuối' — chỉ chạy trên phần DƯ mà regex bó tay.

Tách khỏi `chu_cuoi.py` (thuần stdlib, chạy host được) vì module này import
`vlm_client` (litellm). Gọi VLM ở chế độ TEXT-ONLY (images_b64=[]) nên rẻ hơn
extract ảnh nhiều bậc, KHÔNG tranh GPU vision với pipeline chính.

Chỉ gọi khi regex ra `confidence='thap'` + `nguon='bien_dong'` + `chu=[]` — tức đã
phân loại đúng là chuyển chủ nhưng không moi được người (thừa kế cho con cháu,
bên bán/bên mua lẫn, hộ chiếu thay CCCD…). Ca 'text không có tên' thì LLM cũng
trả [] → giữ nguyên thap, đẩy hậu kiểm.

E2E smoke (cần vLLM endpoint):
    docker compose exec api python -m app.scripts.smoke chu_cuoi_llm --limit 40
"""

from __future__ import annotations

from typing import Any

from src.extentions.multimodal.chu_cuoi import _looks_like_name, _s
from src.extentions.multimodal.vlm_client import chat_json

_SYSTEM = """Bạn bóc DANH SÁCH CHỦ SỬ DỤNG MỚI (người NHẬN) từ một ghi chú biến động \
đất đai tiếng Việt. Chỉ trả JSON.

CHỈ lấy người NHẬN quyền — người được tặng cho / nhận chuyển nhượng / nhận thừa kế / \
được chuyển quyền. Lấy đủ mọi người nhận (có thể nhiều người, gồm con cháu, vợ chồng, \
anh chị em…).

TUYỆT ĐỐI KHÔNG lấy: người CHUYỂN/bên bán/bên tặng, ngân hàng, tổ chức tín dụng, văn \
phòng công chứng, cơ quan nhà nước. Nếu ghi chú KHÔNG nêu tên người nhận nào (chỉ có số \
hợp đồng, số thửa, số giấy tờ mà thiếu họ tên) → trả danh sách RỖNG.

Với mỗi người nhận lấy đúng các trường (thiếu thì để chuỗi rỗng):
  "Tên chủ"      : họ tên đầy đủ, KHÔNG kèm 'Ông/Bà/ông/bà'.
  "Loại giấy tờ" : 'Căn cước công dân' hoặc 'Chứng minh nhân dân' hoặc 'Hộ chiếu' hoặc ''.
  "Số giấy tờ"   : chỉ dãy số/ký tự của giấy tờ người đó.
  "Năm sinh"     : 4 chữ số hoặc ''.
  "Giới tính"    : 'Nam'/'Nữ'/''.
  "Địa chỉ"      : nơi thường trú/địa chỉ, hoặc ''.

Trả đúng cấu trúc: {"chu_nhan": [ {…}, … ]}"""

_USER = """Ghi chú biến động:
\"\"\"{noi_dung}\"\"\"

Trả JSON {{"chu_nhan": [...]}} — chỉ người NHẬN. Không có tên người nhận thì "chu_nhan": []."""

_FIELDS = ("Tên chủ", "Loại giấy tờ", "Số giấy tờ", "Năm sinh", "Giới tính", "Địa chỉ")


def _clean_person(p: Any) -> dict | None:
    """Chuẩn hóa 1 người từ LLM về schema; loại nếu tên không giống tên người."""
    if not isinstance(p, dict):
        return None
    name = _s(p.get("Tên chủ"))
    if not _looks_like_name(name):
        return None
    sg = _s(p.get("Số giấy tờ"))
    loai = _s(p.get("Loại giấy tờ"))
    # Số CCCD/CMND phải 9/12 số; sai (vd model bịa) → bỏ số, GIỮ tên. Hộ chiếu để nguyên.
    if loai != "Hộ chiếu" and sg and not (sg.isdigit() and len(sg) in (9, 12)):
        sg = ""
    return {
        "Tên chủ": name,
        "Loại giấy tờ": loai if loai in ("Căn cước công dân", "Chứng minh nhân dân", "Hộ chiếu") else "",
        "Số giấy tờ": sg,
        "Năm sinh": _s(p.get("Năm sinh")),
        "Giới tính": _s(p.get("Giới tính")) if _s(p.get("Giới tính")) in ("Nam", "Nữ") else "",
        "Địa chỉ": _s(p.get("Địa chỉ")),
    }


async def extract_recipients_llm(noi_dung: Any) -> list[dict]:
    """Bóc người NHẬN từ 1 text biến động bằng LLM text-only. Lỗi → []."""
    text = _s(noi_dung)
    if not text:
        return []
    try:
        d = await chat_json(
            system_prompt=_SYSTEM,
            user_text=_USER.format(noi_dung=text),
            images_b64=[],            # ← TEXT-ONLY: không ảnh, không GPU vision
            enable_thinking=False,
        )
    except Exception:  # noqa: BLE001 — mạng/model lỗi: coi như không bóc được
        return []
    raw = d.get("chu_nhan") if isinstance(d, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[tuple] = set()
    for p in raw:
        c = _clean_person(p)
        if not c:
            continue
        key = (c["Tên chủ"].lower(), c["Số giấy tờ"])
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


async def refine_chu_cuoi(cc: dict, entry: dict) -> dict:
    """Nâng cấp 1 chu_cuoi bằng LLM khi regex bó tay (thap + bien_dong + chu=[]).

    Trả về cc MỚI (không sửa tại chỗ). LLM bóc được → điền chu, confidence='cao',
    đánh dấu method='llm'. Không được → giữ nguyên (vẫn thap).
    """
    if not (isinstance(cc, dict) and cc.get("nguon") == "bien_dong"
            and cc.get("confidence") == "thap" and not cc.get("chu")):
        return cc
    idx = cc.get("bien_dong_index")
    bds = entry.get("Biến động") or []
    if not (isinstance(idx, int) and 0 <= idx < len(bds)):
        return cc
    chu = await extract_recipients_llm(bds[idx].get("Nội dung biến động"))
    if not chu:
        return cc  # LLM cũng chịu → giữ cờ canh_bao cho hậu kiểm
    return {**cc, "chu": chu, "confidence": "cao", "method": "llm", "canh_bao": ""}
