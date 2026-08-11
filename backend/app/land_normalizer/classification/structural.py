"""Phan loai cau truc ho so (1 giay 1/nhieu chu - 1 thua - don/da muc dich - co
chung/rieng) - suy ra TRUC TIEP tu `Payload` da resolve, khong doc them nguon nao,
khong goi API. Xem quytac.md muc "Phan loai hinh thuc ho so".

Cac nhan hien da co quy tac (5 nhan, khop danh sach nguoi dung cung cap 2026-08-06,
tru 2 nhan "Bien dong..." thuoc classification/bien_dong.py):
- "1 giay 1 chu 1 thua" / "1 giay 1 chu 1 thua da muc dich"
- "1 giay nhieu chu 1 thua" / "1 giay nhieu chu 1 thua da muc dich"
- "Co su dung chung, rieng"

Truong hop nhieu hon 1 thua CHUA co nhan chi tiet (danh sach 7 nhan nguoi dung
cung cap chi neu ro "1 thua") - `NhanCauTruc` se ghi 1 dong tong quat thay vi
doan sai."""

from __future__ import annotations

from app.land_normalizer.classification.models import PhanLoaiCauTruc
from app.land_normalizer.models.payload import Payload


def classify_structural(payload: Payload) -> PhanLoaiCauTruc:
    so_chu = len(payload.ChuSuDungSoHuus)
    so_thua = len(payload.ThuaDats)
    co_da_muc_dich = any(len(td.DaMucDichs) > 1 for td in payload.ThuaDats)
    co_chung_rieng = any(
        {md.SuDungChung for md in td.DaMucDichs} == {True, False}
        for td in payload.ThuaDats
        if td.DaMucDichs
    )

    nhan: list[str] = []
    if so_chu > 0 and so_thua == 1:
        chu_label = "nhiều chủ" if so_chu > 1 else "1 chủ"
        muc_dich_suffix = " đa mục đích" if co_da_muc_dich else ""
        nhan.append(f"1 giấy {chu_label} 1 thửa{muc_dich_suffix}")
    elif so_chu > 0 and so_thua > 1:
        nhan.append(f"Nhiều thửa ({so_thua}) - chưa có nhãn phân loại chi tiết cho trường hợp này")

    if co_chung_rieng:
        nhan.append("Có sử dụng chung, riêng")

    return PhanLoaiCauTruc(
        SoChuSoHuu=so_chu,
        NhieuChu=so_chu > 1,
        SoThua=so_thua,
        NhieuThua=so_thua > 1,
        CoDaMucDich=co_da_muc_dich,
        CoSuDungChungVaRieng=co_chung_rieng,
        NhanCauTruc=nhan,
    )
