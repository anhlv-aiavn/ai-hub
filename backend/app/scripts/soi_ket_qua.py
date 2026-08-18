"""Soi thư mục JSON của smoke_e2e_doc_types, tìm dấu hiệu bóc tách sai.

    python -m app.scripts.soi_ket_qua /tmp/full
    python -m app.scripts.soi_ket_qua --tu-kiem

Chỉ đọc file, không gọi VLM, không cần hạ tầng.

Vì sao cần: bảng của smoke chỉ đo CẤU TRÚC (lọc trang, ô trống) và ĐỊNH DẠNG
(ngày dd/mm/yyyy, CCCD 9/12 số). Cả hai đều báo "sạch" cho một giá trị bịa hoàn
toàn. Đã gặp thật: một số CCCD mình lỡ viết làm ví dụ trong prompt bị model chép
ra cho hồ sơ khác — đúng 12 chữ số, qua sạch mọi luật, và giống hệt nhau ở nhiều
hồ sơ. Chính chỗ "giống hệt nhau ở nhiều hồ sơ" là thứ máy phát hiện được còn
mắt người soi từng file thì không.

Cái này KHÔNG chấm được đúng/sai so với giấy. Nó chỉ khoanh vùng hồ sơ đáng ngờ
để người soi ảnh gốc theo thứ tự ưu tiên, thay vì mở lần lượt 90 tệp.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from typing import Any

_RE_NGAY = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_RE_SO_GIAY_TO = re.compile(r"^\d{9}$|^\d{12}$")
_RE_SO = re.compile(r"^\d+(?:\.\d+)?$")
# Dãy 9/12 số đứng riêng, ở BẤT KỲ đâu trong giá trị — dùng để truy số căn cước
# lặp lại giữa các hồ sơ, kể cả khi nó lọt vào một trường không ngờ tới.
_RE_CCCD_TRONG_CAU = re.compile(r"(?<!\d)(\d{9}|\d{12})(?!\d)")

# Trường mà hai hồ sơ khác nhau trùng giá trị là chuyện BẤT THƯỜNG. Cố ý không
# liệt kê Địa chỉ, Diện tích, Mục đích sử dụng, Địa danh, Cơ quan cấp: cùng một
# thôn/xã, cùng loại đất, cùng UBND ký — trùng là bình thường, đưa vào chỉ tổ
# gây nhiễu rồi người soi bỏ qua cả bảng.
_PHAI_DUY_NHAT = (
    "Số giấy tờ", "Giấy tờ nhân thân", "Họ và tên", "Tên chủ",
    "Thửa đất số", "Số thứ tự thửa", "Số văn bản", "Số vào sổ", "Số phát hành",
)


def _la_duy_nhat(duong: str) -> bool:
    return any(t in duong for t in _PHAI_DUY_NHAT)


def phang(nut: Any, tien_to: str = "") -> dict[str, Any]:
    """Cây JSON → {"a.b[].c": giá trị}. Mọi phần tử mảng gộp về cùng một đường,
    vì ta quan tâm "trường này có giá trị gì" chứ không phải nó ở dòng thứ mấy."""
    ra: dict[str, list] = defaultdict(list)

    def di(n: Any, d: str) -> None:
        if isinstance(n, dict):
            for k, v in n.items():
                di(v, f"{d}.{k}" if d else k)
        elif isinstance(n, list):
            for x in n:
                di(x, f"{d}[]")
        else:
            ra[d].append(n)

    di(nut, tien_to)
    return dict(ra)


def dung_dang(truong: str, v: Any) -> bool | None:
    """None = không có luật, hoặc ô trống (không tính vào tỷ lệ)."""
    if not isinstance(v, str) or not v.strip():
        return None
    ten = truong.rsplit(".", 1)[-1].removesuffix("[]")
    if "Ngày" in ten:
        return bool(_RE_NGAY.match(v.strip()))
    if "Số giấy tờ" in ten or "Giấy tờ nhân thân" in ten:
        return bool(_RE_SO_GIAY_TO.match(v.strip()))
    if "Diện tích" in ten:
        return bool(_RE_SO.match(v.strip()))
    # Cột "Loại giấy tờ" của bảng Mẫu 15a phải là CHỮ (CCCD/CMND/Hộ chiếu). Thấy
    # dãy số ở đây nghĩa là model đọc lệch cột, cả hàng trượt sang trái — đã gặp
    # thật ở ddk 1369423/1369458. Kiểm được vì nó là lỗi KIỂU, không cần biết
    # giá trị đúng là gì.
    if "Loại giấy tờ" in ten:
        return not v.strip().replace(".", "").replace(" ", "").isdigit()
    return None


def doc_thu_muc(d: str) -> list[dict]:
    """Mỗi tệp .json của smoke → một bản ghi phẳng để soi."""
    ho_so = []
    for ten in sorted(os.listdir(d)):
        if not ten.endswith(".json"):
            continue
        with open(os.path.join(d, ten), encoding="utf-8") as f:
            doc = json.load(f)
        ma, _, ten_tep = ten[:-5].partition("-")
        exts = doc.get("extractions") or []
        e = exts[0] if exts else {}
        ho_so.append({
            "ma": ma, "ten": ten_tep or ten,
            "trang_giu": len(e.get("page_indices") or []),
            "trang_tong": e.get("page_count") or 0,
            "loi": e.get("error"),
            "o": phang(e.get("result") or {}),
        })
    return ho_so


def _co_gia_tri(v: Any) -> bool:
    """Ô tick trả true/false — CẢ HAI đều là dữ liệu model đã đọc được, khác hẳn
    ô bỏ trống. Đếm sót boolean thì mục "trống ở mọi hồ sơ" báo nhầm cả nhóm
    'Đề nghị' của ddk trong khi JSON có true."""
    if isinstance(v, bool):
        return True
    if isinstance(v, str):
        return bool(v.strip())
    return v is not None


def _o_da_dien(o: dict[str, list]) -> int:
    return sum(1 for vs in o.values() for v in vs if _co_gia_tri(v))


def phan_tich(ho_so: list[dict]) -> dict:
    """Trả về các nhóm nghi vấn. Không kết luận đúng/sai — chỉ xếp thứ tự soi."""
    theo_loai: dict[str, list] = defaultdict(list)
    for h in ho_so:
        theo_loai[h["ma"]].append(h)

    ra: dict[str, Any] = {"loai": {}, "trung_gia_tri": [], "cccd_lap": [], "sai_dang": []}

    # Số 9/12 chữ số xuất hiện ở nhiều hồ sơ — dấu hiệu model chép một con số
    # cố định thay vì đọc giấy. Quét TOÀN BỘ giá trị, không chỉ trường số giấy tờ.
    cccd: dict[str, set] = defaultdict(set)
    gia_tri: dict[tuple, set] = defaultdict(set)
    for h in ho_so:
        khoa = f"{h['ma']}/{h['ten']}"
        for duong, vs in h["o"].items():
            for v in vs:
                if not isinstance(v, str) or not v.strip():
                    continue
                for so in _RE_CCCD_TRONG_CAU.findall(v):
                    cccd[so].add(khoa)
                if _la_duy_nhat(duong):
                    gia_tri[(duong, v.strip())].add(khoa)

    ra["cccd_lap"] = sorted(
        ((so, sorted(ts)) for so, ts in cccd.items() if len(ts) > 1),
        key=lambda x: -len(x[1]))
    # Giá trị ngắn (số thửa "5", tờ bản đồ "45") trùng nhau giữa hai hồ sơ là
    # chuyện thường của cùng một xã — chỉ báo khi trùng từ 3 hồ sơ trở lên. Giá
    # trị dài (họ tên, số giấy tờ) thì trùng 2 lần đã đáng ngờ rồi.
    ra["trung_gia_tri"] = sorted(
        (((d, v), sorted(ts)) for (d, v), ts in gia_tri.items()
         if len(ts) >= (2 if len(v) >= 4 else 3)),
        key=lambda x: -len(x[1]))

    for ma, ds in sorted(theo_loai.items()):
        dien = [_o_da_dien(h["o"]) for h in ds]
        giua = statistics.median(dien) if dien else 0
        # "Lệch hẳn đám đông" = điền dưới 60% mức trung vị của cùng loại giấy.
        # Không dùng độ lệch chuẩn: mẫu 30 file, một vài file hỏng nặng đủ kéo
        # ngưỡng ra xa rồi chính chúng lại lọt.
        ngoai_lai = sorted((h for h in ds if _o_da_dien(h["o"]) < giua * 0.6),
                           key=lambda h: _o_da_dien(h["o"]))
        trang = [h["trang_giu"] for h in ds if h["trang_tong"]]
        t_giua = statistics.median(trang) if trang else 0
        ra["loai"][ma] = {
            "so_ho_so": len(ds),
            "loi": [h["ten"] for h in ds if h["loi"]],
            "dien_trung_vi": giua,
            "ngoai_lai": [(h["ten"], _o_da_dien(h["o"]), h["trang_giu"], h["trang_tong"])
                          for h in ngoai_lai],
            "trang_trung_vi": t_giua,
            "trang_la": [(h["ten"], h["trang_giu"], h["trang_tong"]) for h in ds
                         if h["trang_tong"] and abs(h["trang_giu"] - t_giua) >= 2],
            # TRỐNG Ở MỌI hồ sơ, không phải "trống ở hồ sơ nào đó" — cái sau thì
            # trường nào cũng dính vì luôn có vài tờ dân bỏ trống.
            "trong_het": sorted(
                d for d in set().union(*[set(h["o"]) for h in ds])
                if all(not any(_co_gia_tri(v) for v in h["o"].get(d, [])) for h in ds)),
        }
        for h in ds:
            for duong, vs in h["o"].items():
                for v in vs:
                    if dung_dang(duong, v) is False:
                        ra["sai_dang"].append((ma, h["ten"], duong, v))
    return ra


def in_bao_cao(kq: dict) -> None:
    print("=" * 78)
    print("SOI KẾT QUẢ — khoanh vùng hồ sơ đáng ngờ, KHÔNG phải chấm đúng/sai")
    print("=" * 78)

    for ma, d in kq["loai"].items():
        print(f"\n[{ma}] {d['so_ho_so']} hồ sơ · trung vị {d['dien_trung_vi']:.0f} ô đã điền"
              f" · trung vị {d['trang_trung_vi']:.0f} trang vào VLM")
        if d["loi"]:
            print(f"  LỖI XỬ LÝ ({len(d['loi'])}): {', '.join(d['loi'])}")
        if d["ngoai_lai"]:
            print(f"  Điền ít bất thường ({len(d['ngoai_lai'])}) — soi trước:")
            for ten, n, tg, tt in d["ngoai_lai"]:
                print(f"    {ten:20} {n:3} ô   trang {tg}/{tt}")
        if d["trang_la"]:
            print(f"  Số trang lệch ≥2 so với trung vị ({len(d['trang_la'])}):")
            for ten, tg, tt in d["trang_la"]:
                print(f"    {ten:20} {tg}/{tt}")
        if d["trong_het"]:
            print("  Trường TRỐNG ở MỌI hồ sơ (prompt sai tên trường, hoặc dân không khai):")
            for t in d["trong_het"]:
                print(f"    {t}")

    print(f"\n{'─' * 78}\nSỐ 9/12 CHỮ SỐ LẶP Ở NHIỀU HỒ SƠ — dấu hiệu bịa mạnh nhất")
    if not kq["cccd_lap"]:
        print("  (không có — tốt)")
    for so, ts in kq["cccd_lap"][:20]:
        print(f"  {so}  ×{len(ts)}: {', '.join(ts[:6])}{' …' if len(ts) > 6 else ''}")

    print(f"\n{'─' * 78}\nTRÙNG GIÁ TRỊ Ở TRƯỜNG LẼ RA PHẢI KHÁC NHAU")
    if not kq["trung_gia_tri"]:
        print("  (không có)")
    for (duong, v), ts in kq["trung_gia_tri"][:25]:
        print(f"  {duong} = {v!r}  ×{len(ts)}: {', '.join(ts[:5])}{' …' if len(ts) > 5 else ''}")

    print(f"\n{'─' * 78}\nSAI ĐỊNH DẠNG ({len(kq['sai_dang'])} ô) — mỗi dòng là một ô phải mở giấy")
    for ma, ten, duong, v in kq["sai_dang"][:40]:
        print(f"  [{ma}] {ten:18} {duong} = {v!r}")
    if len(kq["sai_dang"]) > 40:
        print(f"  … còn {len(kq['sai_dang']) - 40} ô nữa")

    print(f"\n{'─' * 78}")
    print("Không mục nào ở trên chứng minh sai. Chúng chỉ xếp thứ tự để soi ảnh gốc.")
    print("Ngược lại: sạch hết cũng KHÔNG chứng minh đúng — model đọc sai một chữ số")
    print("viết tay thì mọi phép kiểm ở đây đều cho qua.")


def _smoke() -> None:
    assert phang({"a": {"b": ""}, "c": [{"d": "x"}, {"d": "y"}]}) == \
        {"a.b": [""], "c[].d": ["x", "y"]}, "KILL [1] phẳng hoá gộp phần tử mảng"
    assert phang({"a": []}) == {}, "KILL [2] mảng rỗng không sinh đường dẫn"

    assert dung_dang("x.Ngày ký", "03/08/2021") is True, "KILL [3]"
    assert dung_dang("x.Ngày ký", "3/8/2021") is False, "KILL [4] thiếu số 0"
    assert dung_dang("x.Ngày ký", "") is None, "KILL [5] ô trống không tính"
    assert dung_dang("x.Giấy tờ nhân thân", "033064004030") is True, "KILL [6]"
    assert dung_dang("a.b[].Số giấy tờ", "03017001916") is False, "KILL [7] 11 số"
    assert dung_dang("x.Địa chỉ", "Thôn 9") is None, "KILL [8] không có luật"
    assert dung_dang("a[].Loại giấy tờ", "CCCD") is True, "KILL [8a] loại giấy tờ là chữ"
    assert dung_dang("a[].Loại giấy tờ", "001078002126") is False, \
        "KILL [8b] dãy số ở cột Loại giấy tờ = model lệch cột"
    # Đúng chỗ từng hụt: luật phải chạm được trường nằm SÂU trong mảng lồng.
    assert dung_dang("Đơn.Người sử dụng chung[].Số giấy tờ", "abc") is False, \
        "KILL [9] trường lồng trong mảng vẫn phải bị kiểm"

    assert _la_duy_nhat("A.Người sử dụng đất.Số giấy tờ"), "KILL [10]"
    assert not _la_duy_nhat("A.Thửa đất.Địa chỉ"), "KILL [11] địa chỉ trùng là bình thường"
    assert not _la_duy_nhat("A.Thửa đất.Diện tích"), "KILL [12] diện tích trùng là bình thường"

    hs = [
        {"ma": "pcctt", "ten": "A", "trang_giu": 1, "trang_tong": 2, "loi": None,
         "o": phang({"P": {"Số giấy tờ": "033064004050", "Tên": "An", "Địa chỉ": "Thôn 9"}})},
        {"ma": "pcctt", "ten": "B", "trang_giu": 1, "trang_tong": 3, "loi": None,
         "o": phang({"P": {"Số giấy tờ": "033064004050", "Tên": "Bình", "Địa chỉ": "Thôn 9"}})},
        {"ma": "pcctt", "ten": "C", "trang_giu": 5, "trang_tong": 6, "loi": None,
         "o": phang({"P": {"Số giấy tờ": "", "Tên": "", "Địa chỉ": ""}})},
    ]
    kq = phan_tich(hs)
    assert kq["cccd_lap"] and kq["cccd_lap"][0][0] == "033064004050", \
        f"KILL [13] phải tóm được số lặp giữa 2 hồ sơ: {kq['cccd_lap']}"
    assert kq["cccd_lap"][0][1] == ["pcctt/A", "pcctt/B"], "KILL [14] nêu đúng hồ sơ nào"
    trung = {d for (d, _), _ in kq["trung_gia_tri"]}
    assert any("Số giấy tờ" in d for d in trung), "KILL [15] trùng ở trường phải duy nhất"
    assert not any("Địa chỉ" in d for d in trung), "KILL [16] đừng báo trùng địa chỉ"
    d = kq["loai"]["pcctt"]
    assert [t for t, *_ in d["ngoai_lai"]] == ["C"], f"KILL [17] hồ sơ trống: {d['ngoai_lai']}"
    assert ("C", 5, 6) in d["trang_la"], f"KILL [18] số trang lệch: {d['trang_la']}"
    assert not d["loi"], "KILL [19] không có lỗi xử lý"

    # Trường có hồ sơ điền, có hồ sơ bỏ trống thì KHÔNG phải "trống ở mọi hồ sơ".
    trong = kq["loai"]["pcctt"]["trong_het"]
    assert not trong, f"KILL [20a] chỉ hồ sơ C trống, không được coi là trống toàn bộ: {trong}"
    hs_trong = [dict(h, o=phang({"P": {"X": "", "Y": "co"}})) for h in hs[:2]]
    assert phan_tich(hs_trong)["loai"]["pcctt"]["trong_het"] == ["P.X"], \
        "KILL [20b] trường trống ở MỌI hồ sơ mới được nêu"

    # Số thửa ngắn trùng 2 hồ sơ là bình thường; trùng 3 mới đáng nêu.
    ngan = [dict(h, ten=t, o=phang({"P": {"Thửa đất số": "5"}}))
            for h, t in zip(hs[:2], "AB")]
    assert not phan_tich(ngan)["trung_gia_tri"], "KILL [20c] giá trị ngắn trùng 2 lần: bỏ qua"
    ngan3 = ngan + [dict(hs[0], ten="C", o=phang({"P": {"Thửa đất số": "5"}}))]
    assert phan_tich(ngan3)["trung_gia_tri"], "KILL [20d] trùng 3 lần thì phải nêu"

    # Ô tick false vẫn là dữ liệu: model đã nhìn thấy ô và xác định là chưa tick.
    tick = [dict(hs[0], ten=t, o=phang({"P": {"Cấp GCN": c, "Ghi nợ": False}}))
            for t, c in (("A", True), ("B", False))]
    assert phan_tich(tick)["loai"]["pcctt"]["trong_het"] == [], \
        "KILL [20e] boolean là giá trị, KHÔNG phải ô trống"
    assert _o_da_dien(phang({"x": False})) == 1, "KILL [20f] false vẫn tính là đã điền"
    assert _o_da_dien(phang({"x": ""})) == 0, "KILL [20g] chuỗi rỗng thì không"

    kq2 = phan_tich([hs[0]])
    assert not kq2["cccd_lap"], "KILL [20] một hồ sơ thì không thể gọi là lặp"
    print("soi_ket_qua PURE: 29 KILL ✓")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("thu_muc", nargs="?", help="Thư mục JSON do smoke --json sinh ra.")
    ap.add_argument("--tu-kiem", action="store_true", help="Chạy PURE smoke rồi thoát.")
    a = ap.parse_args()
    if a.tu_kiem:
        _smoke()
    elif not a.thu_muc:
        ap.error("cần đường dẫn thư mục JSON, hoặc --tu-kiem")
    elif not os.path.isdir(a.thu_muc):
        print(f"không thấy thư mục {a.thu_muc!r}", file=sys.stderr)
        sys.exit(2)
    else:
        hs = doc_thu_muc(a.thu_muc)
        if not hs:
            print(f"{a.thu_muc!r} không có tệp .json nào", file=sys.stderr)
            sys.exit(2)
        in_bao_cao(phan_tich(hs))
