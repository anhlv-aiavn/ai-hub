"""Dry-run ĐỐI CHỨNG prompt Số phát hành (SPH) — TRƯỚC khi re-extract đại trà.

Trả lời HAI câu, không chỉ một:
  1. Prompt mới có SỬA được ca hỏng không?  (nhóm mẫu đang sai/rỗng SPH)
  2. Có LÀM SAI giấy đang đúng không?        (nhóm mẫu đang hợp lệ SPH — chống hồi quy)

Cách đo: lấy mẫu doc thật từ Mongo, render ảnh (đúng DPI production), chạy LẠI
trích xuất với prompt hiện tại trong image, rồi so danh sách SPH cũ (đang lưu) với
mới (vừa chạy) theo TỪNG hồ sơ. CHỈ ĐỌC — không ghi Mongo, không ghi MinIO.

Vì sao tách nhóm ≤ DETECT_MIN_PAGES trang: file ≤5 trang đi nhánh "1 file = 1
giấy" (bỏ detect), nếu chuyên viên lỡ up tài liệu KHÔNG PHẢI giấy thì vẫn bị ép
trích xuất → SPH rác. Re-extract KHÔNG cứu được (bản thân không là GCN) — phải
tách ra khỏi phép đo, không thì đổ oan cho prompt là "chưa sửa".

CẢNH BÁO TRANH TẢI: gọi VLM thật, cùng endpoint worker. Chạy khi worker đang cày
thì số bẩn. Dừng worker hoặc chấp nhận.

Chạy TRONG CONTAINER (api có Mongo + MinIO + VLLM_*):
    # 25 ca hỏng + 25 ca đúng, dùng prompt nhẹ (extract_gcn_only):
    docker compose exec api python -m app.scripts.audit_sph_prompt --n-hong 25 --n-dung 25
    # soi vài hồ sơ CỤ THỂ mình biết là sai:
    docker compose exec api python -m app.scripts.audit_sph_prompt --file "Số O 646324,776451,769181"
    # test bằng prompt ĐẦY ĐỦ (đường requeue toàn phần) thay vì prompt nhẹ:
    docker compose exec api python -m app.scripts.audit_sph_prompt --day-du --n-hong 30
"""

import argparse
import asyncio
import os
import re
import time
from collections import Counter

# ── Hợp lệ SPH — regex THUẦN, tự chứa (không import extract_gcn: nó kéo make/
# pdfium/onnx nên smoke sẽ không chạy được ở máy dev). Giữ ĐỒNG BỘ với
# extract_gcn._VALID_SPH_* — nếu đổi ở đó thì đổi cả đây.
_VALID_DIGITS = re.compile(r"^\d{8,15}$")
_VALID_LETTER = re.compile(r"^[A-ZĐ]{1,4} ?\d{5,}$")
DETECT_MIN_PAGES = int(os.getenv("DETECT_MIN_PAGES", "5"))


def _valid(s) -> bool:
    if not isinstance(s, str):
        return False
    s = s.strip().upper()
    return bool(s and (_VALID_DIGITS.fullmatch(s) or _VALID_LETTER.fullmatch(s)))


def _sph_stored(records) -> list:
    """Danh sách SPH đang LƯU của 1 doc (theo thứ tự record → entry)."""
    out = []
    for r in records or []:
        res = r.get("result") if isinstance(r, dict) else None
        if not isinstance(res, dict):
            continue
        for e in res.get("Đăng ký") or []:
            g = e.get("Giấy chứng nhận") if isinstance(e, dict) else None
            if isinstance(g, dict):
                out.append(g.get("Số phát hành"))
    return out


def _sph_new(result: dict, day_du: bool) -> list:
    """SPH từ kết quả chạy lại. day_du=True → extract() ({"Đăng ký":[{Giấy chứng
    nhận: dict}]}); False → extract_gcn_only() ({"Giấy chứng nhận":[...]})."""
    if day_du:
        out = []
        for e in (result or {}).get("Đăng ký") or []:
            g = e.get("Giấy chứng nhận") if isinstance(e, dict) else None
            if isinstance(g, dict):
                out.append(g.get("Số phát hành"))
        return out
    return [it.get("Số phát hành") for it in (result or {}).get("Giấy chứng nhận") or []
            if isinstance(it, dict)]


def _doi_chieu(old_list: list, new_list: list) -> dict:
    """So MULTISET các SPH HỢP LỆ cũ vs mới. Trả các đếm để xếp loại hồ sơ.
    Dùng multiset (Counter) chứ không set: 1 hồ sơ có thể có nhiều GCN cùng dạng."""
    ov = Counter(s.strip().upper() for s in old_list if _valid(s))
    nv = Counter(s.strip().upper() for s in new_list if _valid(s))
    return {
        "old_hople": sum(ov.values()),
        "new_hople": sum(nv.values()),
        "mat": sum((ov - nv).values()),      # SPH hợp lệ CŨ biến mất ở bản mới → hồi quy
        "them": sum((nv - ov).values()),     # SPH hợp lệ MỚI có thêm → sửa được
        "n_gcn": max(len(old_list), len(new_list)),
    }


def _verdict_hong(d: dict) -> str:
    """Nhóm ĐANG HỎNG: có thêm SPH hợp lệ = tiến bộ."""
    if d["them"] > 0 and d["new_hople"] > d["old_hople"]:
        return "SỬA ĐƯỢC"
    if d["new_hople"] > 0 and d["old_hople"] == 0:
        return "SỬA ĐƯỢC"
    return "chưa sửa"


def _verdict_dung(d: dict) -> str:
    """Nhóm ĐANG ĐÚNG: mất bất kỳ SPH hợp lệ cũ nào = HỒI QUY (điều phải tránh)."""
    return "HỒI QUY" if d["mat"] > 0 else "giữ nguyên"


# ── Chạy 1 doc: render + trích xuất lại (import nặng để TRONG hàm) ────────────

async def _chay_doc(doc: dict, day_du: bool, sem) -> dict:
    import functools
    import io

    from app import storage
    from src.extentions.multimodal.extract_gcn import extract, extract_gcn_only
    from src.extentions.multimodal.make import pdf_to_corrected_images

    RENDER_DPI = int(os.getenv("AIHUB_RENDER_DPI", "200"))
    RENDER_MAX = int(os.getenv("AIHUB_RENDER_MAX_SIZE", "2000"))

    old_list = _sph_stored(doc.get("extractions") or [])
    loop = asyncio.get_running_loop()
    buf = await storage.get_pdf(doc["s3_key"], doc.get("source_connection_id"))
    render = functools.partial(pdf_to_corrected_images, dpi=RENDER_DPI, max_img_size=RENDER_MAX)
    images = await loop.run_in_executor(None, render, buf)
    if not images:
        return {"loi": "render rỗng", "old_list": old_list, "new_list": []}

    async with sem:
        result = await (extract(images) if day_du else extract_gcn_only(images))
    new_list = _sph_new(result, day_du)
    return {"old_list": old_list, "new_list": new_list, "n_trang": len(images), "loi": None}


# ── Lấy mẫu: pool ngẫu nhiên → chia HỎNG/ĐÚNG theo SPH đang lưu ───────────────

_SPH_PROJ = {"s3_key": 1, "source_connection_id": 1, "filename": 1, "page_count": 1,
             "extractions.result.Đăng ký.Giấy chứng nhận.Số phát hành": 1}


def _co_hong(records) -> bool:
    sph = _sph_stored(records)
    return (not sph) or any(not _valid(s) for s in sph)


async def _lay_mau(gcns, pool: int, n_hong: int, n_dung: int, files):
    """Trả (list doc hỏng, list doc đúng). `files` → lấy đích danh, bỏ qua pool."""
    if files:
        pats = [re.escape(f.strip()) for f in files if f.strip()]
        q = {"filename": {"$regex": "|".join(pats)}} if pats else {}
        docs = await gcns.find(q, _SPH_PROJ).limit(200).to_list(length=200)
        hong = [d for d in docs if _co_hong(d.get("extractions"))]
        dung = [d for d in docs if not _co_hong(d.get("extractions"))]
        return hong, dung

    # $sample: mẫu NGẪU NHIÊN toàn kho (không lấy đầu ổ đĩa = 1 địa phương).
    pipeline = [{"$match": {"status": "done", "s3_key": {"$exists": True}}},
                {"$sample": {"size": pool}}, {"$project": _SPH_PROJ}]
    hong, dung = [], []
    async for d in gcns.aggregate(pipeline, allowDiskUse=True):
        if _co_hong(d.get("extractions")):
            if len(hong) < n_hong:
                hong.append(d)
        elif len(dung) < n_dung:
            dung.append(d)
        if len(hong) >= n_hong and len(dung) >= n_dung:
            break
    return hong, dung


def _fmt(xs: list) -> str:
    return "[" + ", ".join(repr(x) for x in xs) + "]" if xs else "[]"


def _in_hang(tag: str, r: dict, doc: dict) -> None:
    ngan = " ·≤5trang" if (doc.get("page_count") or r.get("n_trang") or 99) <= DETECT_MIN_PAGES else ""
    print(f"  [{tag:9}]{ngan:9} {str(doc.get('filename'))[:30]:<32}"
          f" {_fmt(r['old_list'])[:40]:<42} → {_fmt(r['new_list'])[:40]}")


async def run(pool, n_hong, n_dung, day_du, song_song, files) -> None:
    from app import config
    from app.db import gcns as gcns_col

    gcns = gcns_col()
    print("  đang lấy mẫu…", flush=True)
    hong, dung = await _lay_mau(gcns, pool, n_hong, n_dung, files)
    print(f"  mẫu: {len(hong)} hỏng · {len(dung)} đúng · prompt="
          f"{'ĐẦY ĐỦ' if day_du else 'nhẹ (gcn_only)'}\n", flush=True)
    if not hong and not dung:
        print("  không lấy được mẫu nào — kiểm tra --file / kho rỗng?")
        return

    sem = asyncio.Semaphore(config.MAX_VLM_CONCURRENT)
    cong = asyncio.Semaphore(max(1, song_song))
    st = Counter()

    async def _one(doc, cohort):
        async with cong:
            try:
                r = await _chay_doc(doc, day_du, sem)
            except Exception as e:  # noqa: BLE001
                print(f"  ! {doc.get('filename')}: {e}", flush=True)
                st["loi"] += 1
                return
        if r["loi"]:
            st["loi"] += 1
            return
        d = _doi_chieu(r["old_list"], r["new_list"])
        ngan = (doc.get("page_count") or r.get("n_trang") or 99) <= DETECT_MIN_PAGES
        if cohort == "hong":
            v = _verdict_hong(d)
            st[f"hong_{v}"] += 1
            if ngan and v == "chưa sửa":
                st["hong_ngan_chua_sua"] += 1   # nghi KHÔNG phải giấy, không tính vào lỗi prompt
            _in_hang(v, r, doc)
        else:
            v = _verdict_dung(d)
            st[f"dung_{v}"] += 1
            _in_hang(v, r, doc)

    print("── NHÓM ĐANG HỎNG (mong: SỬA ĐƯỢC) " + "─" * 34)
    await asyncio.gather(*(_one(d, "hong") for d in hong))
    print("\n── NHÓM ĐANG ĐÚNG (mong: giữ nguyên) " + "─" * 32)
    await asyncio.gather(*(_one(d, "dung") for d in dung))

    _tong_ket(st, len(hong), len(dung))


def _tong_ket(st, n_hong, n_dung) -> None:
    print("\n" + "=" * 68)
    print(" KẾT QUẢ DRY-RUN PROMPT SỐ PHÁT HÀNH")
    print("=" * 68)
    sua = st["hong_SỬA ĐƯỢC"]
    chua = st["hong_chưa sửa"]
    print(f" NHÓM HỎNG ({n_hong}):  sửa được {sua}  ·  chưa sửa {chua}")
    if st["hong_ngan_chua_sua"]:
        print(f"    trong 'chưa sửa' có {st['hong_ngan_chua_sua']} hồ sơ ≤{DETECT_MIN_PAGES} trang"
              "  ← nhiều khả năng KHÔNG phải giấy, prompt không lỗi ở đây")
    hoi_quy = st["dung_HỒI QUY"]
    giu = st["dung_giữ nguyên"]
    print(f" NHÓM ĐÚNG ({n_dung}):  giữ nguyên {giu}  ·  HỒI QUY {hoi_quy}")
    if hoi_quy:
        print("    ⚠ CÓ HỒI QUY — prompt mới làm mất SPH đang đúng. XEM kỹ trước khi áp rộng.")
    else:
        print("    ✓ không ca đúng nào bị phá.")
    if st["loi"]:
        print(f" lỗi (render/mạng/VLM): {st['loi']}")
    print("\n  Đọc: nhóm hỏng 'sửa được' cao + nhóm đúng 0 hồi quy ⇒ prompt an toàn để")
    print("  build đại trà. Còn hồi quy ⇒ dừng, xem từng ca hồi quy ở trên.")


# ── PURE smoke ───────────────────────────────────────────────────────────────

def _smoke() -> None:
    assert _valid("O 646324"), "KILL [1] O 646324 phải hợp lệ (chữ+6 số)"
    assert _valid("0103040010"), "KILL [2] 10 số thuần phải hợp lệ"
    assert not _valid("646324"), "KILL [3] 6 số cụt phải KHÔNG hợp lệ"
    assert not _valid(""), "KILL [4] rỗng không hợp lệ"
    assert not _valid(None), "KILL [5] None không hợp lệ"

    stored = [{"result": {"Đăng ký": [
        {"Giấy chứng nhận": {"Số phát hành": "646324"}},
        {"Giấy chứng nhận": {"Số phát hành": "DD 999053"}}]}}]
    assert _sph_stored(stored) == ["646324", "DD 999053"], f"KILL [6] {_sph_stored(stored)}"

    # extract_gcn_only: {"Giấy chứng nhận":[...]}
    r_nhe = {"Giấy chứng nhận": [{"Số phát hành": "O 646324"}, {"Số phát hành": "DD 999053"}]}
    assert _sph_new(r_nhe, False) == ["O 646324", "DD 999053"], "KILL [7] parse gcn_only sai"
    # extract đầy đủ: {"Đăng ký":[{Giấy chứng nhận: dict}]}
    r_du = {"Đăng ký": [{"Giấy chứng nhận": {"Số phát hành": "O 646324"}}]}
    assert _sph_new(r_du, True) == ["O 646324"], "KILL [8] parse full sai"

    # HỎNG → SỬA ĐƯỢC: 646324 (cụt) thành O 646324 (hợp lệ)
    d = _doi_chieu(["646324"], ["O 646324"])
    assert _verdict_hong(d) == "SỬA ĐƯỢC", f"KILL [9] {d}"
    # HỎNG vẫn hỏng: cụt → cụt
    assert _verdict_hong(_doi_chieu(["646324"], ["646324"])) == "chưa sửa", "KILL [10]"
    # ĐÚNG → giữ nguyên: hợp lệ vẫn còn nguyên value
    assert _verdict_dung(_doi_chieu(["DD 999053"], ["DD 999053"])) == "giữ nguyên", "KILL [11]"
    # ĐÚNG → HỒI QUY: value hợp lệ cũ biến mất
    assert _verdict_dung(_doi_chieu(["DD 999053"], ["646324"])) == "HỒI QUY", "KILL [12]"
    # ĐÚNG → HỒI QUY: value đổi khác (đọc nhầm ra SPH khác nhưng vẫn hợp lệ hình thức)
    assert _verdict_dung(_doi_chieu(["DD 999053"], ["DD 999054"])) == "HỒI QUY", "KILL [13]"
    # multiset: 2 GCN cùng dạng, mất 1 vẫn là hồi quy
    assert _verdict_dung(_doi_chieu(["A 11111", "A 11111"], ["A 11111"])) == "HỒI QUY", "KILL [14]"

    print("audit_sph_prompt PURE: 14 KILL ✓")


async def main() -> None:
    p = argparse.ArgumentParser(description="Dry-run đối chứng prompt Số phát hành (read-only).")
    p.add_argument("--n-hong", type=int, default=25, help="Số ca ĐANG HỎNG lấy mẫu.")
    p.add_argument("--n-dung", type=int, default=25, help="Số ca ĐANG ĐÚNG lấy mẫu (chống hồi quy).")
    p.add_argument("--pool", type=int, default=800, help="Cỡ pool $sample để lọc ra 2 nhóm.")
    p.add_argument("--day-du", action="store_true", help="Dùng prompt ĐẦY ĐỦ (extract) thay vì nhẹ.")
    p.add_argument("--song-song", type=int, default=4, help="Số hồ sơ chạy đồng thời.")
    p.add_argument("--file", default=None, help="Lấy đích danh theo tên tệp (phân tách phẩy).")
    p.add_argument("--smoke", action="store_true", help="Chạy PURE smoke rồi thoát.")
    args = p.parse_args()
    if args.smoke:
        _smoke()
        return
    files = args.file.split(",") if args.file else None
    await run(args.pool, args.n_hong, args.n_dung, args.day_du, args.song_song, files)


if __name__ == "__main__":
    import sys

    if "--smoke" in sys.argv:
        _smoke()  # KHÔNG qua asyncio — giữ smoke thuần stdlib, chạy được ở máy dev
    else:
        asyncio.run(main())
