"""Shape ket qua phan loai cau truc ho so - xem quytac.md muc "Phan loai hinh
thuc ho so".

KHONG phai 1 phan cua Payload/payload.json (hop dong co dinh voi backend, xem
models/payload.py) - day la dau ra CUA RIENG buoc phan loai.

LUU Y: du an goc (vpdd-don-ai) con co them "Phan loai noi dung bien dong" qua
Claude API (classification/bien_dong.py, BienDongPhanLoai/PhanLoaiHoSo) - PHAN
NAY KHONG duoc port sang ai-hub (yeu cau: bo hoan toan buoc phan loai bang AI),
chi giu lai phan cau truc (mien phi, thuan, khong goi API nao)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PhanLoaiCauTruc(BaseModel):
    """Phan loai cau truc (so chu/so thua/da muc dich/chung-rieng) - suy ra TRUC
    TIEP tu Payload da resolve, khong can OCR/LLM them nen luon tinh duoc ngay khi
    co payload (chi phi = 0, khong goi API nao)."""

    model_config = ConfigDict(populate_by_name=True)

    SoChuSoHuu: int
    NhieuChu: bool
    SoThua: int
    NhieuThua: bool
    CoDaMucDich: bool
    CoSuDungChungVaRieng: bool
    NhanCauTruc: list[str] = Field(default_factory=list)
