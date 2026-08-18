"""Xuất một trang HTML đặt ẢNH TRANG cạnh TRƯỜNG ĐÃ BÓC để người soi tự đối chiếu.

    python -m app.scripts.doi_chieu --loai pcctt /work/.../576827.pdf --ra /tmp/soi.html
    python -m app.scripts.doi_chieu --tu-kiem

Vì sao cần: mọi phép đo hiện có chỉ kiểm ĐỊNH DẠNG. Một số căn cước 12 chữ số
sai đúng một chữ số vẫn qua sạch. Muốn biết bóc ra có khớp giấy không thì chỉ có
cách nhìn — và nhìn nhanh được thì mới nhìn nổi vài chục hồ sơ.

Trang HTML tự chứa (ảnh nhúng base64), mở bằng trình duyệt bất kỳ, không cần
mạng. Ô nào sai định dạng theo luật của soi_ket_qua thì tô đỏ sẵn để soi trước.
"""
from __future__ import annotations

import argparse
import asyncio
import html
import io
import os
import sys

from app import doc_types
from app.scripts.soi_ket_qua import dung_dang, phang

_CSS = """
:root{--vien:#d6d9de;--do:#c0392b;--nen-do:#fdecea;--mo:#6b7280}
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:#111}
header{padding:10px 16px;border-bottom:1px solid var(--vien);position:sticky;top:0;background:#fff;z-index:2}
header b{font-size:16px}
header span{color:var(--mo);margin-left:10px}
.khung{display:grid;grid-template-columns:1fr 1fr;gap:0;align-items:start}
.anh{border-right:1px solid var(--vien);padding:12px;position:sticky;top:49px;max-height:calc(100vh - 49px);overflow:auto}
.anh img{width:100%;margin-bottom:12px;border:1px solid var(--vien)}
.anh .nhan{font-size:12px;color:var(--mo);margin-bottom:4px}
.bang{padding:12px}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:12px;color:var(--mo);font-weight:600;padding:10px 6px 4px;border-bottom:1px solid var(--vien)}
td{padding:5px 6px;border-bottom:1px solid #eee;vertical-align:top}
td.ten{width:44%;color:#374151}
td.gt{font-weight:500;white-space:pre-wrap;word-break:break-word}
tr.xau td{background:var(--nen-do)}
tr.xau td.gt{color:var(--do)}
tr.xau td.gt::after{content:" ← sai định dạng";font-weight:400;font-size:12px}
td.trong{color:#9ca3af}
@media (max-width:900px){.khung{grid-template-columns:1fr}.anh{position:static;max-height:none;border-right:0}}
"""


def _hang(duong: str, v) -> str:
    ten = html.escape(duong)
    if isinstance(v, bool):
        gt, lop = ("có (đã tick)" if v else "không"), "gt"
    elif v is None or (isinstance(v, str) and not v.strip()):
        gt, lop = "— trống —", "gt trong"
    else:
        gt, lop = html.escape(str(v)), "gt"
    xau = " class=\"xau\"" if dung_dang(duong, v) is False else ""
    return f'<tr{xau}><td class="ten">{ten}</td><td class="{lop}">{gt}</td></tr>'


def lam_html(ten_tep: str, ma: str, images: list[str], rec: dict) -> str:
    """images = ảnh base64 của TRANG ĐÃ ĐƯA VÀO VLM (đúng thứ nó nhìn thấy)."""
    idx = rec.get("page_indices") or []
    anh = "".join(
        f'<div class="nhan">trang {i + 1}/{rec.get("page_count") or "?"}</div>'
        f'<img src="data:image/jpeg;base64,{b64}">'
        for i, b64 in zip(idx, images))
    if not anh:
        anh = '<p style="color:#6b7280">Không có trang nào được đưa vào VLM.</p>'

    if rec.get("error"):
        than = (f'<tr class="xau"><td class="ten">LỖI</td>'
                f'<td class="gt">{html.escape(str(rec["error"]))}</td></tr>')
    else:
        o = phang(rec.get("result") or {})
        than = "".join(_hang(d, v) for d in sorted(o) for v in o[d]) or \
            '<tr><td colspan="2" class="trong">Không bóc được trường nào.</td></tr>'

    bo = (rec.get("page_count") or 0) - len(idx)
    return f"""<!doctype html><html lang="vi"><meta charset="utf-8">
<title>{html.escape(ten_tep)} · {html.escape(ma)}</title><style>{_CSS}</style>
<header><b>{html.escape(ten_tep)}</b><span>{html.escape(ma)} ·
{len(idx)}/{rec.get("page_count") or "?"} trang vào VLM{f" · bỏ {bo} trang đính kèm" if bo > 0 else ""}</span></header>
<div class="khung">
  <div class="anh">{anh}</div>
  <div class="bang"><table>
    <tr><th>Trường</th><th>Giá trị bóc ra</th></tr>{than}
  </table>
  <p style="color:#6b7280;font-size:12px;margin-top:14px">Ô tô đỏ = sai ĐỊNH DẠNG.
  Ô không tô vẫn có thể SAI NỘI DUNG — chỉ mắt bạn so với ảnh bên trái mới biết.</p>
  </div>
</div>"""


async def _chay(duong: str, ma: str) -> tuple[list[str], dict]:
    from app.worker.run_job import _pipeline   # nặng, chỉ nạp khi chạy thật
    dt = doc_types.get(ma)
    with open(duong, "rb") as f:
        buf = io.BytesIO(f.read())
    records, images = await _pipeline(buf, None, dt)
    rec = records[0] if records else {"page_indices": [], "result": {}, "page_count": 0}
    dt.normalize([rec])
    idx = rec.get("page_indices") or []
    return [images[i] for i in idx if 0 <= i < len(images)], rec


def _smoke() -> None:
    rec = {"page_indices": [0], "page_count": 3, "error": None,
           "result": {"P": {"Số giấy tờ": "12345", "Tên": "Nguyễn Văn A",
                            "Địa chỉ": "", "Cấp GCN": True}}}
    h = lam_html("a.pdf", "pcctt", ["QUJD"], rec)

    assert "data:image/jpeg;base64,QUJD" in h, "KILL [1] ảnh nhúng thẳng, không tải ngoài"
    assert "1/3 trang vào VLM" in h and "bỏ 2 trang" in h, f"KILL [2] nêu số trang đã bỏ"
    assert h.count("<img") == 1, "KILL [3] chỉ hiện trang ĐƯA VÀO VLM, không hiện trang đã loại"
    assert '<tr class="xau"><td class="ten">P.Số giấy tờ' in h, \
        "KILL [4] ô sai định dạng phải tô đỏ sẵn"
    assert '<tr><td class="ten">P.Tên' in h, "KILL [5] ô hợp lệ thì không tô"
    assert "— trống —" in h, "KILL [6] ô trống hiện rõ, không để khoảng trắng"
    assert "có (đã tick)" in h, "KILL [7] boolean đọc được bằng tiếng Việt"
    assert "SAI NỘI DUNG" in h, \
        "KILL [8] phải nói rõ tô đỏ chỉ là định dạng — nếu không người soi sẽ tin ô trắng là đúng"

    # Chèn HTML từ dữ liệu giấy tờ: phải bị vô hiệu, không được chạy.
    doc = lam_html("x.pdf", "ddk", [], {"page_indices": [], "page_count": 0,
                                        "result": {"A": "<script>alert(1)</script>"}})
    assert "<script>alert" not in doc and "&lt;script&gt;" in doc, "KILL [9] thoát HTML"

    loi = lam_html("y.pdf", "pcctt", [], {"page_indices": [], "page_count": 1,
                                          "error": "VLM chết", "result": None})
    assert "VLM chết" in loi and "xau" in loi, "KILL [10] hồ sơ lỗi hiện lỗi, không hiện bảng rỗng"
    print("doi_chieu PURE: 10 KILL ✓")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("duong", nargs="?", help="Đường dẫn PDF (trong container là /work/...).")
    ap.add_argument("--loai", choices=sorted(doc_types.MA_HOP_LE), help="Loại giấy.")
    ap.add_argument("--ra", default="/tmp/doi_chieu.html", help="Tệp HTML đích (%(default)s).")
    ap.add_argument("--tu-kiem", action="store_true", help="Chạy PURE smoke rồi thoát.")
    a = ap.parse_args()
    if a.tu_kiem:
        _smoke()
        sys.exit(0)
    if not a.duong or not a.loai:
        ap.error("cần đường dẫn PDF và --loai")
    if not os.path.isfile(a.duong):
        print(f"không thấy tệp {a.duong!r}", file=sys.stderr)
        sys.exit(2)
    imgs, rec = asyncio.run(_chay(a.duong, a.loai))
    with open(a.ra, "w", encoding="utf-8") as f:
        f.write(lam_html(os.path.basename(a.duong), a.loai, imgs, rec))
    print(f"ghi {a.ra}  ({len(imgs)} trang, "
          f"{'LỖI: ' + str(rec['error']) if rec.get('error') else 'ok'})")
