"""Resolver cho GiayChungNhans.

CHI resolve `GiayChungNhanInfo` (mo ta ban than giay chung nhan: so hieu, ngay vao
so, loai giay...) tu OCR. CHUA resolve `PhapNhanId`/`ThuaDatIds`/`DaMucDichIds`/
`NhaIds` - xem "GIOI HAN KIEN TRUC" o cuoi file.

=== NGUON `SoHieuGiayChungNhan` (sua lai 2026-08-07 - thay the quy tac 2026-08-03) ===

2 nguon: `so_gcn` ("tt" - du lieu thu thap ngoai thuc dia, cot dau tien, KHONG
phai OCR) va `ai_gcn_so_phat_hanh` ("OCR" - doc tu anh GCN). Ca 2 THUONG XUYEN
lech nhau (xem lich su cu: ~14% OCR rong hoan toan, ~37% con lai khac `so_gcn`
tren 1 file 2231 dong that - OCR thieu/thua tien to chu, doc nham chu so, hoac
nham sang van ban khac).

Quy tac MOI (nguoi dung cung cap 2026-08-07, thay quy tac "luon uu tien so_gcn"
cu):
1. **tt == OCR** (giong nhau, so sanh sau khi strip khoang trang) -> dung tt lam
   ket qua, KHONG canh bao (day la truong hop bthg, 2 nguon dong thuan).
2. **tt != OCR** (xung dot) -> kiem tra tung ben co phan loai duoc "Loai GCN"
   hop le khong (`_classify_loai_gcn`, xem quy tac ben duoi):
   - tt phan loai duoc -> dung tt.
   - tt KHONG phan loai duoc nhung OCR phan loai duoc -> dung OCR.
   - CA HAI deu khong phan loai duoc -> fallback ve tt (mac dinh).
   - Trong CA 3 truong hop tren (xung dot), ghi canh bao vao `FieldResult.note`
     (tien to "XUNG DOT so hieu:" de frontend nhan dien hien hieu ung canh bao o
     trang chi tiet don) va `FieldResult.source` = `"tt"`/`"ocr"` de frontend
     hien icon nguon dang dung (xem GiayChungNhanSection.vue).
3. 1 trong 2 rong -> dung ben con lai, khong tinh la xung dot (khong canh bao).
4. Ca 2 rong -> `FieldResult.missing`.

"Loai GCN" duoc phan loai LAI tren `SoHieuGiayChungNhan` CUOI CUNG (sau khi da
quyet dinh o tren) - tuc thay doi theo dung mo ta nguoi dung: "Loại giấy chứng
nhận: suy theo số hiệu giấy" (xem quy tac phan loai ben duoi, khong doi).

=== Phan loai "Loai GCN" (quy tac cung cap 2026-08-01) ===

Dua vao dinh dang `SoHieuGiayChungNhan` (gio la `so_gcn`, xem tren) va ngay cap
(tu OCR "Ngày cấp", `ai_gcn_ngay_cap`):

1. Luat Dat Dai 1993: 1 chu cai + 6 so (vd "B123456").
2. Luat Dat Dai 2003: bat dau bang 'A', 2 chu cai (AB, AC, AD...) + 6 so.
3. NĐ 88/NĐ-CP: 2 chu cai KHAC 2003 (bat dau tu B, C, D... khong phai A) + 6 so,
   cap TRUOC 01/07/2014.
4. NĐ 43/NĐ-CP: cung dinh dang 2 chu cai (B, C, D...) + 6 so nhu tren, nhung cap
   SAU 01/07/2014.
5. NĐ 60/NĐ-CP: so hieu la 1 dai 11 chu so, ngay cap TRUOC 06/09/2006.
6. NĐ 90/NĐ-CP: so hieu la 1 dai 15 chu so, ngay cap SAU 06/09/2006.

Du lieu thuc te co khoang trang giua phan chu va phan so (vd "AE 993401",
"CG 748902") - `_classify_loai_gcn` bo qua khoang trang truoc khi doi chieu mau.

=== MAU THUAN DA PHAT HIEN - CAN NGUOI DUNG XAC NHAN ===

`payload.json` mau ban dau (GCN `10101070103`) co `SoHieuGiayChungNhan =
"10101070103"` (11 chu so) va `NgayVaoSoISO8601 = "2000-08-15"` (TRUOC
06/09/2006) - theo dung quy tac tren, day PHAI la NĐ 60/NĐ-CP. Nhung
`payload.json` mau lai ghi `TenLoaiGiayChungNhan = "Giấy chứng nhận QSHNƠ &
QSDĐƠ theo Nghị định 90/NĐ-CP"` (NĐ 90) va `LoaiGiayChungNhanId = 4`,
`MaLoaiGiayChungNhan = "GCN QSD NHÀ Ở VÀ QSD ĐẤT Ở 90"`.

Resolver nay UU TIEN tuan theo quy tac vua duoc cung cap (ro rang, co chu dich)
hon vi du cu (co the la ban nhap truoc khi co quy tac chinh thuc - da tung gap
tinh huong tuong tu voi ChuSuDungSoHuus, xem lich-su-phat-trien.md). Ket qua:
GCN nay se duoc resolver phan loai la NĐ 60, khac vi du mau. CAN NGUOI DUNG XAC
NHAN LAI xem quy tac hay vi du mau moi la dung. (Doi nguon sang so_gcn ngay
2026-08-03 KHONG anh huong ket luan nay - o dong nay so_gcn == ai_gcn_so_phat_hanh
== "10101070103".)

=== Bang tra cuu LoaiGiayChungNhanId / MaLoaiGiayChungNhan (cap nhat 2026-08-05) ===

Nguoi dung cung cap catalog `loaiGcn` (xem `app.land_normalizer.catalogs.LOAI_GCN`). Da
doi chieu thu cong tung khoa noi bo (`luat_1993`, `luat_2003`, `nd_88`, `nd_43`,
`nd_60`, `nd_90`) voi `ten_loai` cua catalog (giong het hoac chi khac cach viet tat
"Nghị định" vs "NĐ") va xac nhan ca 6 loai deu khop duoc 1 muc catalog duy nhat - xem
`_LOAI_GCN_CATALOG_ID` (khoa -> id catalog) va `_LOAI_GCN_INFO` (tra `catalogs.
loai_gcn_by_id` de lay `LoaiGiayChungNhanId`/`MaLoaiGiayChungNhan`/`TenLoaiGiayChungNhan`)
duoi day. `TenLoaiGiayChungNhan` xuat ra gio la van ban `ten_loai` cua catalog (thay
cho van ban tu viet truoc day), thay vi hardcode rieng NĐ 90 nhu truoc.

=== GIOI HAN KIEN TRUC: chua resolve PhapNhanId/ThuaDatIds/DaMucDichIds/NhaIds ===

Cac field nay can THAM CHIEU sang cac object da resolve o CHO KHAC trong CUNG 1
payload (vd ThuaDatIds can biet ThuaDats da duoc resolve ra Id gi) - nhung
`ResolverContext` hien chi mang `attempts` (du lieu nguon), KHONG mang payload
dang duoc dung dang (xem resolvers/base.py). Day la gioi han kien truc thuc su,
khong phai thieu sot: can thiet ke them (vd ResolverContext mang them payload
dang xay, hoac 1 buoc "lien ket" chay SAU khi moi whole-list resolver da chay
xong) truoc khi lam duoc phan nay - chua lam vi chua co yeu cau/quy tac cho no.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date

from app.land_normalizer import catalogs
from app.land_normalizer.models.payload import GiayChungNhan, GiayChungNhanInfo
from app.land_normalizer.resolvers.base import FieldResult, ResolverContext
from app.land_normalizer.resolvers.support import first_non_empty, parse_vn_date, parse_vn_date_obj

_RE_TWO_LETTER = re.compile(r"^([A-Z])[A-Z]\d{6}$")
_RE_ONE_LETTER = re.compile(r"^[A-Z]\d{6}$")
_RE_ALL_DIGITS = re.compile(r"^\d+$")
_RE_LEADING_INT = re.compile(r"^\s*(\d+)")

_ND_88_43_CUTOFF = date(2014, 7, 1)
_ND_60_90_CUTOFF = date(2006, 9, 6)

# Khoa noi bo (tra ve boi _classify_loai_gcn) -> id trong catalogs.LOAI_GCN. Da doi
# chieu thu cong 2026-08-05 giua van ban mo ta cua tung khoa va `ten_loai` catalog -
# ca 6 loai deu khop duoc 1 muc catalog.
_LOAI_GCN_CATALOG_ID: dict[str, int] = {
    "luat_1993": 2,
    "luat_2003": 1,
    "nd_88": 6,
    "nd_43": 11,
    "nd_60": 3,
    "nd_90": 4,
}

_LOAI_GCN_INFO: dict[str, dict[str, object]] = {}
for _key, _catalog_id in _LOAI_GCN_CATALOG_ID.items():
    _entry = catalogs.loai_gcn_by_id(_catalog_id)
    assert _entry is not None, f"catalogs.LOAI_GCN thieu id={_catalog_id} (khoa {_key!r})"
    _LOAI_GCN_INFO[_key] = {
        "LoaiGiayChungNhanId": _entry["id"],
        "MaLoaiGiayChungNhan": _entry["ma_loai"],
        "TenLoaiGiayChungNhan": _entry["ten_loai"],
    }


def _classify_loai_gcn(so_hieu: str, ngay_cap: date | None) -> tuple[str | None, str | None]:
    """Tra ve (khoa noi bo de tra _LOAI_GCN_INFO, ghi chu). Khoa noi bo KHONG phai
    gia tri xuat ra payload."""
    compact = re.sub(r"\s+", "", so_hieu or "").upper()
    if not compact:
        return None, "SoHieuGiayChungNhan rong"

    two_letter = _RE_TWO_LETTER.match(compact)
    if two_letter:
        if two_letter.group(1) == "A":
            return "luat_2003", None
        if ngay_cap is None:
            return None, "2 chu cai (khac 2003) + 6 so nhung thieu ngay cap de phan biet ND88/ND43"
        return ("nd_43" if ngay_cap >= _ND_88_43_CUTOFF else "nd_88"), None

    if _RE_ONE_LETTER.match(compact):
        return "luat_1993", None

    if _RE_ALL_DIGITS.match(compact):
        if ngay_cap is None:
            return None, f"So hieu toan chu so ({len(compact)} so) nhung thieu ngay cap de phan loai ND60/ND90"
        if len(compact) == 11:
            if ngay_cap < _ND_60_90_CUTOFF:
                return "nd_60", None
            return None, "So hieu 11 chu so nhung ngay cap sau 06/09/2006 - khong khop mau nao (ND60 can truoc moc nay)"
        if len(compact) == 15:
            if ngay_cap >= _ND_60_90_CUTOFF:
                return "nd_90", None
            return None, "So hieu 15 chu so nhung ngay cap truoc 06/09/2006 - khong khop mau nao (ND90 can sau moc nay)"
        return None, f"So hieu toan chu so nhung co {len(compact)} chu so (khong phai 11 hay 15)"

    return None, None


def _parse_so_vao_so(raw: str | None) -> tuple[int | None, str | None]:
    """SoVaoSo phai la int theo contract (truoc day la string) - nhung OCR doi khi
    tra ve chuoi co hau to la (vd '13079 2000', thay trong 00004.xlsx - nghi la so
    vao so bi dinh them nam cap ke ben, '2000' trung voi nam cua NgayVaoSoISO8601 o
    cung dong). Lay CHU SO DAU TIEN (`_RE_LEADING_INT`), ghi lai gia tri OCR goc vao
    note khi khac voi ket qua da parse de con nguoi review (khong am tham cat bo)."""
    if not raw:
        return None, None
    match = _RE_LEADING_INT.match(raw)
    if not match:
        return None, f"SoVaoSo (OCR) khong bat dau bang chu so, khong parse duoc: {raw!r}"
    parsed = int(match.group(1))
    if match.group(0).strip() != raw.strip():
        return parsed, f"SoVaoSo (OCR) co hau to bi cat bo khi ep sang int: {raw!r} -> {parsed}"
    return parsed, None


def _resolve_so_hieu(tt_raw: str | None, ocr_raw: str | None, ngay_cap: date | None) -> tuple[str, str, str | None]:
    """Quyet dinh SoHieuGiayChungNhan cuoi cung tu 2 nguon: "tt" (`so_gcn` - du
    lieu thu thap ngoai thuc dia) va "ocr" (`ai_gcn_so_phat_hanh` - doc tu anh
    GCN). Quy tac chi tiet xem docstring dau file (muc "NGUON SoHieuGiayChungNhan").

    Tra ve (so_hieu, source, canh_bao):
    - source: "tt" hoac "ocr" - FE dung de hien icon nguon dang dung.
    - canh_bao: khac None CHI KHI 2 nguon xung dot (khac nhau, ca 2 deu co du
      lieu) - FE dung de hien hieu ung canh bao o trang chi tiet don. Ca 2 rong
      tra ve so_hieu="" (goi ham xu ly rieng, tra FieldResult.missing)."""
    tt = (tt_raw or "").strip()
    ocr = (ocr_raw or "").strip()

    if tt and ocr and tt != ocr:
        tt_loai_key, _ = _classify_loai_gcn(tt, ngay_cap)
        if tt_loai_key:
            so_hieu, source = tt, "tt"
        else:
            ocr_loai_key, _ = _classify_loai_gcn(ocr, ngay_cap)
            so_hieu, source = (ocr, "ocr") if ocr_loai_key else (tt, "tt")
        # "source" giu nguyen ma ngan "tt"/"ocr" (dung de FE so sanh/hien icon) -
        # note (hien qua tooltip hover) dung nhan day du "Tu nhap" thay "tt" cho
        # de hieu voi nguoi dung (theo yeu cau nguoi dung 2026-08-07).
        source_label = "Tu nhap" if source == "tt" else "OCR"
        warning = f"XUNG DOT so hieu: Tu nhap={tt!r} vs OCR={ocr!r} - da chon nguon={source_label!r}"
        return so_hieu, source, warning

    if tt:
        return tt, "tt", None
    if ocr:
        return ocr, "ocr", None
    return "", "tt", None


@dataclass
class GiayChungNhansResolver:
    field_path: str = "GiayChungNhans"

    def resolve(self, ctx: ResolverContext) -> FieldResult:
        ocr_so_phat_hanh, _ = first_non_empty(ctx.attempts, lambda r: r.ai_gcn_so_phat_hanh)

        if not ctx.so_gcn and not ocr_so_phat_hanh:
            # Thuc te gan nhu khong xay ra (xem docstring dau file) - giu de
            # phong thu, khong gia dinh so_gcn luon co.
            return FieldResult.missing(
                note="Khong co so_gcn (tt) lan ai_gcn_so_phat_hanh (OCR) o bat ky attempt nao"
            )

        so_vao_so_raw, _ = first_non_empty(ctx.attempts, lambda r: r.ai_gcn_so_vao_so)
        so_vao_so, so_vao_so_note = _parse_so_vao_so(so_vao_so_raw)
        ngay_cap_raw, _ = first_non_empty(ctx.attempts, lambda r: r.ai_gcn_ngay_cap)
        ngay_cap_date = parse_vn_date_obj(ngay_cap_raw)

        so_hieu, source, conflict_note = _resolve_so_hieu(ctx.so_gcn, ocr_so_phat_hanh, ngay_cap_date)

        # "Loai GCN" phan loai LAI tren SoHieuGiayChungNhan CUOI CUNG (sau khi da
        # quyet dinh tt/ocr o tren) - dung theo yeu cau "Loại giấy chứng nhận:
        # suy theo số hiệu giấy".
        loai_key, classify_note = _classify_loai_gcn(so_hieu, ngay_cap_date)
        loai_info = _LOAI_GCN_INFO.get(loai_key, {}) if loai_key else {}

        info = GiayChungNhanInfo(
            Id=str(uuid.uuid4()),
            LoaiGiayChungNhanId=loai_info.get("LoaiGiayChungNhanId"),
            MaLoaiGiayChungNhan=loai_info.get("MaLoaiGiayChungNhan"),
            TenLoaiGiayChungNhan=loai_info.get("TenLoaiGiayChungNhan"),
            SoHieuGiayChungNhan=so_hieu,
            SoHoSoGoc=None,
            SoVaoSo=so_vao_so,
            SoVaoSoCu=None,
            NgayVaoSoISO8601=parse_vn_date(ngay_cap_raw),
            TinhTrangGiayChungNhan=None,
            DaCongNhanPhapLy=None,
        )
        entry = GiayChungNhan(
            GiayChungNhan=info,
            PhapNhanId=None,
            ThuaDatIds=[],
            DaMucDichIds=[],
            NhaIds=[],
        )

        note = " ; ".join(n for n in (conflict_note, classify_note, so_vao_so_note) if n) or None
        confidence = 0.85 if loai_key else 0.5
        return FieldResult(value=[entry], confidence=confidence, source=source, note=note)


RESOLVERS = [GiayChungNhansResolver()]
