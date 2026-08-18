"""Sinh schema JSON của 4 loại giấy để bên ngoài đồng bộ cấu trúc.

    python -m app.scripts.xuat_schema              # ghi vào docs/schema/
    python -m app.scripts.xuat_schema --tu-kiem    # PURE, không ghi gì

Vì sao đọc ngược từ prompt chứ không khai lại một bảng trường riêng: prompt là
thứ DUY NHẤT quyết định model trả về cấu trúc gì. Khai một bản sao ở chỗ khác
thì đến lúc ai đó sửa prompt mà quên sửa bản sao, tài liệu phát cho đối tác sẽ
mô tả một API không tồn tại. Hôm nay đã dính đúng kiểu lệch đó hai lần (tên
trường CCCD của ddk, và số mẫu trong prompt rò ra kết quả) nên không lặp lại.

Cái script này KHÔNG gọi VLM, không cần Mongo/MinIO — chỉ đọc chuỗi prompt.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import zipfile
from typing import Any

from app import doc_prompts, doc_types
from src.extentions.multimodal.mdsdd import KEY_MA, KEY_TEN
from src.extentions.multimodal.prompt import extract_system_prompt as gcn_prompt

# Khối JSON mẫu trong mọi prompt đều bắt đầu bằng "{" đứng riêng đầu dòng. Không
# dùng dấu "{" đầu tiên trong chuỗi: prompt GCN có regex "^\d{10,15}$" đứng trước.
_MO_KHOI = re.compile(r"^\{", re.M)

# Trường được chuẩn hoá sau khi model trả về (app/doc_types.py) — ghi vào
# description để bên nhận biết giá trị họ thấy đã qua xử lý, và xử lý thế nào.
_GHI_CHU_CHUAN_HOA = {
    "ngay": 'Chuẩn hoá về "dd/mm/yyyy" khi đọc được (nhận cả dạng "ngày 10 tháng 8 '
            'năm 2026"). Người dân bỏ trống mỗi ô NGÀY mà vẫn ghi tháng/năm → trả '
            '"mm/yyyy" (10 ký tự rút còn 7) — BÊN NHẬN PHẢI XỬ LÝ ĐƯỢC CẢ HAI ĐỘ DÀI. '
            'Ô trống hoàn toàn → "". Không nhận dạng được → giữ nguyên văn chữ trên giấy.',

    "dien_tich": 'Chuẩn hoá về số thập phân dấu chấm, bỏ đơn vị ("120,5 m²" → "120.5"). '
                 "Không parse được → giữ nguyên văn.",
    "so_giay_to": "Chuẩn hoá về dãy số trần 9 (CMND) hoặc 12 (CCCD) chữ số, bỏ dấu phân "
                  "nhóm; nhặt được cả khi người viết ghi thêm chữ quanh số. Không ra "
                  "đúng 9/12 số → GIỮ NGUYÊN VĂN. Giá trị không đúng 9/12 số là tín "
                  "hiệu cần hậu kiểm, KHÔNG được tự sửa cho đủ.",
}


def khung_tu_prompt(prompt: str) -> dict:
    """Nhặt khối JSON mẫu ra khỏi prompt bằng cách đếm ngoặc, có tôn trọng chuỗi
    và ký tự thoát — prompt GCN còn cả đoạn "Examples output" đứng sau khối JSON
    nên không thể cứ cắt tới cuối chuỗi."""
    m = _MO_KHOI.search(prompt)
    if not m:
        raise ValueError("không thấy khối JSON mẫu (dòng bắt đầu bằng '{')")
    i = m.start()
    sau = trong_chuoi = False
    muc = 0
    for j in range(i, len(prompt)):
        c = prompt[j]
        if sau:
            sau = False
        elif c == "\\":
            sau = True
        elif c == '"':
            trong_chuoi = not trong_chuoi
        elif not trong_chuoi:
            if c == "{":
                muc += 1
            elif c == "}":
                muc -= 1
                if muc == 0:
                    return json.loads(prompt[i:j + 1])
    raise ValueError("khối JSON mẫu thiếu dấu đóng ngoặc")


def _ghi_chu(ten: str) -> str | None:
    if "Ngày" in ten:
        return _GHI_CHU_CHUAN_HOA["ngay"]
    if "Diện tích" in ten:
        return _GHI_CHU_CHUAN_HOA["dien_tich"]
    if any(t in ten for t in doc_types._TEN_SO_GIAY_TO):
        return _GHI_CHU_CHUAN_HOA["so_giay_to"]
    return None


def sang_schema(nut: Any, ten: str = "") -> dict:
    """Khung mẫu → JSON Schema. Mảng rỗng trong mẫu nghĩa là "danh sách chuỗi";
    mảng có một object mẫu nghĩa là "danh sách bản ghi theo object đó"."""
    if isinstance(nut, dict):
        thuoc_tinh = {k: sang_schema(v, k) for k, v in nut.items()}
        return {
            "type": "object",
            "properties": thuoc_tinh,
            "required": list(nut),
            "additionalProperties": False,
        }
    if isinstance(nut, list):
        items = sang_schema(nut[0]) if nut else {"type": "string"}
        return {"type": "array", "items": items}
    if isinstance(nut, int) and not isinstance(nut, bool):
        return {"type": ["integer", "string"],
                "description": 'Mã số danh mục. Không map được → "".'}
    if isinstance(nut, bool):
        # Ô tick trên giấy. Model được dặn trả true/false, nhưng vẫn gặp trường
        # hợp trả chuỗi rỗng khi không nhìn thấy ô — nhận cả hai cho khỏi vỡ.
        return {"type": ["boolean", "string"],
                "description": 'Ô tick: true/false. Không xác định được → "".'}
    s: dict = {"type": "string"}
    gc = _ghi_chu(ten)
    if gc:
        s["description"] = gc
    return s


def _hau_xu_ly_gcn(khung: dict, trong: bool = False) -> None:
    """Chèn hai cột do pipeline GCN gán THÊM sau khi model trả về (mdsdd.py) vào
    khung mẫu, tại chỗ: Đăng ký[].Thửa đất[].Mục đích sử dụng[].

    Bắt buộc phải có, vì schema khai `additionalProperties: false` — thiếu hai
    cột này là tài liệu nói dối về chính dữ liệu ta gửi đi. Lấy tên cột từ hằng
    số của mdsdd chứ không gõ lại, để đổi tên bên đó thì file này đổi theo.

    KEY_MA là SỐ id danh mục, nhưng khi không map được thì mdsdd trả "" — nên
    kiểu là integer HOẶC string, không phải integer thuần."""
    for dk in khung.get("Đăng ký", []):
        for thua in dk.get("Thửa đất", []):
            for md in thua.get("Mục đích sử dụng", []):
                md[KEY_MA] = "" if trong else 0
                md[KEY_TEN] = ""


def _bo_schema(ma: str, prompt: str) -> tuple[dict, dict]:
    dt = doc_types.get(ma)
    khung = khung_tu_prompt(prompt)
    if ma == "gcn":
        _hau_xu_ly_gcn(khung)
    goc = sang_schema(khung)
    if ma == "gcn":
        # Khung dùng số 0 chỉ để sang_schema suy ra kiểu integer. File ví dụ thì
        # phải là "" như mọi ô trống khác, chứ 0 không phải mã danh mục có thật.
        _hau_xu_ly_gcn(khung, trong=True)
    goc["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    goc["$id"] = f"https://ai-hub.tumiki.org/schema/{ma}.schema.json"
    goc["title"] = f"{dt.nhan} ({ma})"
    goc["description"] = (
        f"Cấu trúc `result` của một bản ghi trích xuất, loại giấy `{ma}` — {dt.nhan}. "
        f"Sinh tự động từ prompt đang chạy bằng app/scripts/xuat_schema.py; đừng sửa tay."
    )
    return khung, goc


LOAI = ["gcn", "ddk", "kqdk", "pcctt"]


def _prompt(ma: str) -> str:
    if ma == "gcn":
        return gcn_prompt
    return getattr(doc_prompts, f"{ma}_extract_system_prompt")


def _readme() -> str:
    dong = "\n".join(
        f"| `{ma}` | {doc_types.get(ma).nhan} | `{list(khung_tu_prompt(_prompt(ma)))[0]}` "
        f"| {'✅' if doc_types.get(ma).bat else '⏸ tạm tắt'} "
        f"| [{ma}.schema.json]({ma}.schema.json) · [{ma}.example.json]({ma}.example.json) |"
        for ma in LOAI
    )
    return f"""# Schema trích xuất AI-HUB

Sinh tự động — **đừng sửa tay**. Sửa prompt trong `backend/app/doc_prompts.py`
(hoặc `backend/src/extentions/multimodal/prompt.py` cho `gcn`) rồi chạy lại:

```bash
python -m app.scripts.xuat_schema
```

## Bốn loại giấy

| `doc_type` | Tên giấy | Khoá bọc ngoài của `result` | Trạng thái | Schema |
|---|---|---|---|---|
{dong}

`doc_type` là tham số của API, mặc định `gcn` nếu không truyền (giữ tương thích
với dữ liệu cũ). Giá trị lạ → HTTP 400.

Loại **tạm tắt** không gửi lên được nữa (API trả 400) nhưng schema vẫn giữ ở đây,
vì dữ liệu đã bóc trước đó vẫn nằm trong hệ thống và vẫn theo đúng cấu trúc này.

- `POST /v1/batches` — form field `doc_type`
- `POST /v1/browse/{{source_id}}/import` — JSON field `doc_type`
- `GET  /v1/gcn?doc_type=<ma>` — lọc theo loại

## Lấy dữ liệu ra

`GET /v1/gcn/{{gcn_id}}` trả về một hồ sơ. Phần cấu trúc theo loại giấy nằm ở
`extractions[].result`, và **đó là thứ các schema ở đây mô tả**:

```jsonc
{{
  "_id": "…", "batch_id": "…", "filename": "…", "doc_type": "pcctt",
  "status": "done",                  // queued | running | done | error
  "group_key": "2026/Phieu CCTT/576827",
  "summary": {{ … }},                 // tóm tắt phẳng, xem bên dưới
  "extractions": [
    {{
      "page_indices": [0],            // trang (0-based) đã đưa vào VLM
      "page_count": 4,                // tổng số trang của PDF gốc
      "result": {{ … }},               // ← THEO SCHEMA Ở ĐÂY
      "error": null,
      "skip_reason": null
    }}
  ]
}}
```

`summary` là bản phẳng dùng chung cho mọi loại giấy, để hiển thị bảng:
`khoa_chinh`, `so_phat_hanh`, `so_vao_so`, `ngay_cap`, `chu_su_dung[]`,
`to_ban_do[]`, `so_thua[]`, `gcn_count`. Với 3 loại biểu mẫu thì `khoa_chinh`
lấy từ **tên tệp/prefix nguồn**, không phải từ nội dung giấy — mấy loại này
không có số hiệu riêng nào đáng tin trên mặt giấy.

## Ba điều bên nhận cần biết trước khi lập trình

**Ô trống trả `""`, không phải `null`.** Mảng rỗng là `[]`. Không có key nào bị
lược bỏ — cấu trúc luôn đủ trường như trong `*.example.json`.

**Giá trị đã qua chuẩn hoá, nhưng chuẩn hoá KHÔNG bao giờ ép.** Ngày, diện tích
và số giấy tờ được đưa về dạng chuẩn *khi đọc được*; đọc không ra thì **giữ
nguyên văn chữ trên giấy** chứ không trả rỗng. Xem `description` của từng trường
trong schema. Vì vậy đừng khai kiểu chặt (`date`, `number`) ở phía nhận — mọi
trường đều là chuỗi, kể cả trường ngày và trường số.

**Định dạng đúng không có nghĩa nội dung đúng.** Đây là dữ liệu viết tay được
model đọc; một giá trị hợp lệ vẫn có thể sai so với giấy. Luồng nghiệp vụ phải
có bước hậu kiểm, đừng coi kết quả là dữ liệu đã chốt.
"""


def xuat(thu_muc: str) -> list[str]:
    os.makedirs(thu_muc, exist_ok=True)
    ra = []
    for ma in LOAI:
        khung, schema = _bo_schema(ma, _prompt(ma))
        for ten, noi_dung in ((f"{ma}.schema.json", schema), (f"{ma}.example.json", khung)):
            d = os.path.join(thu_muc, ten)
            with open(d, "w", encoding="utf-8") as f:
                json.dump(noi_dung, f, ensure_ascii=False, indent=2)
                f.write("\n")
            ra.append(d)
    d = os.path.join(thu_muc, "README.md")
    with open(d, "w", encoding="utf-8") as f:
        f.write(_readme())
    ra.append(d)
    ra.append(dong_zip(thu_muc))
    return ra


# Ngày giờ CỐ ĐỊNH trong zip: zip lưu mtime từng file nên chạy lại cùng nội dung
# vẫn ra bytes khác → git báo thay đổi giả, và không biết zip có khớp thư mục
# hay không. Cố định thì zip là hàm thuần của nội dung.
_NGAY_ZIP = (2026, 1, 1, 0, 0, 0)


def dong_zip(thu_muc: str) -> str:
    """Đóng gói thư mục schema thành <thu_muc>.zip — đây là thứ THẬT SỰ gửi cho
    đối tác. Sinh cùng lúc với schema để không bao giờ lệch: đã một lần zip còn
    giữ ba trường đã bỏ, mà nhìn thư mục thì thấy đúng."""
    dich = os.path.join(os.path.dirname(os.path.abspath(thu_muc)), "schema.zip")
    ten_goc = os.path.basename(os.path.abspath(thu_muc))
    with zipfile.ZipFile(dich, "w", zipfile.ZIP_DEFLATED) as z:
        for ten in sorted(os.listdir(thu_muc)):
            if ten.startswith(".") or ten.endswith(".zip"):
                continue
            with open(os.path.join(thu_muc, ten), "rb") as f:
                noi_dung = f.read()
            info = zipfile.ZipInfo(f"{ten_goc}/{ten}", date_time=_NGAY_ZIP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, noi_dung)
    return dich


def _smoke() -> None:
    p = 'blah ^\\d{10,15}$ blah\nReturn EXACTLY this JSON structure:\n\n{\n "A": {"b": ""}\n}\n===\nsau'
    assert khung_tu_prompt(p) == {"A": {"b": ""}}, "KILL [1] bỏ qua '{' trong regex, cắt đúng khối"
    assert khung_tu_prompt('{\n "x": "có { ngoặc } trong chuỗi"\n}') == \
        {"x": "có { ngoặc } trong chuỗi"}, "KILL [2] ngoặc trong chuỗi không tính"
    for xau in ("không có json", '{\n "thieu": ""\n'):
        try:
            khung_tu_prompt(xau)
        except ValueError:
            pass
        else:
            raise AssertionError(f"KILL [3] phải báo lỗi rõ: {xau!r}")

    s = sang_schema({"Ngày ký": "", "Diện tích": "", "Số giấy tờ": "",
                     "Giấy tờ nhân thân": "", "Địa chỉ": "", "Cấp GCN": True,
                     "Kèm theo": [], "Người chung": [{"Tên": ""}]})
    pr = s["properties"]
    assert s["required"] == list(pr) and s["additionalProperties"] is False, \
        "KILL [4] mọi trường bắt buộc, cấm trường lạ"
    assert "dd/mm/yyyy" in pr["Ngày ký"]["description"], "KILL [5] ghi chú chuẩn hoá ngày"
    assert "m²" in pr["Diện tích"]["description"], "KILL [6] ghi chú chuẩn hoá diện tích"
    # Cả hai tên gọi của ô CCCD phải cùng được chú thích — đúng chỗ từng lệch.
    for t in ("Số giấy tờ", "Giấy tờ nhân thân"):
        assert "9" in pr[t]["description"] and "12" in pr[t]["description"], \
            f"KILL [7] thiếu ghi chú CCCD cho {t}"
    assert "description" not in pr["Địa chỉ"], "KILL [8] trường không có luật thì đừng bịa ghi chú"
    assert pr["Cấp GCN"]["type"] == ["boolean", "string"], "KILL [9] ô tick nhận cả hai kiểu"
    assert pr["Kèm theo"] == {"type": "array", "items": {"type": "string"}}, \
        "KILL [10] mảng rỗng = danh sách chuỗi"
    assert pr["Người chung"]["items"]["properties"]["Tên"] == {"type": "string"}, \
        "KILL [11] mảng có mẫu = danh sách bản ghi"

    for ma in LOAI:
        khung, schema = _bo_schema(ma, _prompt(ma))
        assert len(khung) == 1, f"KILL [12] {ma}: đúng một khoá bọc ngoài, thấy {list(khung)}"
        assert schema["properties"], f"KILL [13] {ma}: schema rỗng"
        assert doc_types.get(ma).nhan in schema["title"], f"KILL [14] {ma}: title sai tên giấy"
    # Hai cột mdsdd gán thêm sau khi model trả về — thiếu là schema nói dối,
    # vì additionalProperties=false bảo bên nhận rằng không còn trường nào khác.
    gcn_khung, gcn_schema = _bo_schema("gcn", _prompt("gcn"))
    md = (gcn_schema["properties"]["Đăng ký"]["items"]["properties"]["Thửa đất"]
          ["items"]["properties"]["Mục đích sử dụng"]["items"])
    for k in (KEY_MA, KEY_TEN):
        assert k in md["properties"] and k in md["required"], \
            f"KILL [18] schema gcn thiếu cột hậu xử lý {k!r}"
    assert md["properties"][KEY_MA]["type"] == ["integer", "string"], \
        "KILL [19] Mã MĐSD là số, nhưng không map được thì mdsdd trả rỗng"

    # zip phải là hàm THUẦN của nội dung: chạy hai lần ra bytes y hệt, nếu không
    # thì mỗi lần sinh lại git báo thay đổi giả và không ai biết zip có còn khớp
    # thư mục hay không.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "schema")
        os.makedirs(d)
        with open(os.path.join(d, "a.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        z1 = open(dong_zip(d), "rb").read()
        z2 = open(dong_zip(d), "rb").read()
        assert z1 == z2, "KILL [20] zip phải tất định (đóng băng mtime)"
        import zipfile as _z
        with _z.ZipFile(os.path.join(tmp, "schema.zip")) as zf:
            assert zf.namelist() == ["schema/a.json"], \
                f"KILL [21] zip giữ đúng 1 cấp thư mục, không kèm rác: {zf.namelist()}"
        # Chạy lại lần nữa không được nhét chính file zip vào trong zip.
        assert "schema.zip" not in " ".join(_z.ZipFile(dong_zip(d)).namelist()), \
            "KILL [22] không tự đóng gói chính nó"

    # Khoá bọc ngoài phải khớp thứ worker thật đi tìm khi đọc kết quả ra.
    assert list(khung_tu_prompt(_prompt("pcctt")))[0] == "Phiếu thu thập", \
        "KILL [15] khoá bọc ngoài pcctt lệch với doc_types"
    assert list(khung_tu_prompt(_prompt("ddk")))[0] == "Đơn đăng ký", \
        "KILL [16] khoá bọc ngoài ddk lệch với doc_types"
    assert list(khung_tu_prompt(_prompt("kqdk")))[0] == "Giấy xác nhận", \
        "KILL [17] khoá bọc ngoài kqdk lệch với doc_types"
    print("xuat_schema PURE: 22 KILL ✓")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ra", default="docs/schema", help="Thư mục đích (%(default)s).")
    ap.add_argument("--tu-kiem", action="store_true", help="Chạy PURE smoke rồi thoát.")
    a = ap.parse_args()
    if a.tu_kiem:
        _smoke()
    else:
        for d in xuat(a.ra):
            print("ghi", d)
