"""Phase 0.1b — soi CHUỖI NHÃN từng trang để biết vì sao file bị cắt sai.

`groups_from_roles` chỉ là hệ quả; nguyên nhân nằm ở nhãn `classify_page` trả về
cho từng trang. Script này in ra chuỗi nhãn ĐẦY ĐỦ + nhóm suy ra + trang bị rớt,
rồi CHẨN ĐOÁN nguyên nhân — thứ mà nhìn kết quả trong Mongo không thấy được.

Render ĐÚNG THAM SỐ PRODUCTION (dpi=200, max_img_size=2000). KHÔNG hạ dpi: ảnh
hạ nét làm nhãn sai → chẩn đoán sai bệnh.

CHỈ ĐỌC. Không ghi Mongo, không ghi MinIO.

Chạy TRONG CONTAINER:
    # soi 1 file cụ thể (copy file vào container trước)
    docker compose cp ./0161849.pdf api:/tmp/x.pdf
    docker compose exec api python -m app.scripts.audit_cut_vlm --file /tmp/x.pdf

    # soi hồ sơ ĐÃ XỬ LÝ — kéo PDF gốc từ MinIO, so nhãn với nhóm ĐÃ LƯU.
    # Cách tiện nhất: tra bằng Số phát hành in trên bìa, khỏi copy file, khỏi biết id.
    docker compose exec api python -m app.scripts.audit_cut_vlm --ten 0161849
    docker compose exec api python -m app.scripts.audit_cut_vlm --sph "D 0161849"
    docker compose exec api python -m app.scripts.audit_cut_vlm --gcn-id <id>

    # soi cả danh sách nghi ngờ (mỗi dòng 1 gcn_id, do audit_cut_offline xuất)
    docker compose exec api python -m app.scripts.audit_cut_vlm --ids-file /tmp/nghi.txt --limit 100

    # THỬ HÀNG LOẠT ca tương tự sau khi sửa prompt:
    docker compose exec api python -m app.scripts.audit_cut_vlm --mau 30              # ca NGHI cắt sai
    docker compose exec api python -m app.scripts.audit_cut_vlm --mau 30 --doi-chung  # ca đang ĐÚNG
Đọc: cột "đã lưu trong Mongo … [LỆCH]" = prompt mới ĐỔI kết quả. Với --mau thì
LỆCH là điều MONG ĐỢI (sửa được ca hỏng); với --doi-chung thì LỆCH là BÁO ĐỘNG
(đang phá ca vốn đúng).
"""

import argparse
import asyncio
import functools
import io
import os
import re
from collections import Counter

from app import config, storage
from app.db import gcns
from src.extentions.multimodal.detect_gcn import classify_page, groups_from_roles
from src.extentions.multimodal.make import pdf_to_corrected_images

DETECT_MIN_PAGES = int(os.getenv("DETECT_MIN_PAGES", "5"))
RENDER_DPI = int(os.getenv("AIHUB_RENDER_DPI", "200"))
RENDER_MAX_SIZE = int(os.getenv("AIHUB_RENDER_MAX_SIZE", "2000"))

_MARK = {"cover": "▣ BÌA    ", "content": "│ nội dung", "other": "✗ KHÁC   "}


async def _roles_of(images: list[str], vlm: int) -> list[str]:
    sem = asyncio.Semaphore(vlm)

    async def _one(img: str) -> str:
        async with sem:
            try:
                return await classify_page(img)
            except Exception as e:  # noqa: BLE001
                return f"ERR:{e}"[:40]

    return list(await asyncio.gather(*(_one(i) for i in images)))


def _dropped(roles: list[str], groups: list[list[int]]) -> list[int]:
    """Trang NỘI DUNG bị vứt khỏi mọi nhóm = thiệt hại thật.

    Trang 'other' bị loại là ĐÚNG Ý ĐỒ (CCCD, tờ khai, trang trắng) → không tính.
    """
    covered = {i for g in groups for i in g}
    return sorted(i for i in range(len(roles)) if i not in covered and roles[i] != "other")


def _diagnose(roles: list[str], groups: list[list[int]]) -> list[str]:
    """Suy nguyên nhân từ chuỗi nhãn (logic THUẦN — không I/O, test được rời)."""
    out: list[str] = []
    n = len(roles)
    dropped = _dropped(roles, groups)

    # Lỗi 1: `other` GIỮA bộ làm đóng nhóm → mọi trang sau rớt tới hết file.
    for i, r in enumerate(roles):
        if r == "other" and 0 < i < n - 1 and any(j in dropped for j in range(i + 1, n)):
            sau = [j for j in range(i + 1, n) if j in dropped and roles[j] == "content"]
            if sau:
                out.append(f"LỖI 1 — trang {i+1} bị gán 'other' ĐÓNG NHÓM, "
                           f"kéo theo trang {[j+1 for j in sau]} (nội dung thật) bị VỨT")

    # (Đã bỏ cờ "nhóm ngắn bám đuôi = nghi CCCD-thành-bìa": nó báo động giả trên
    # mọi hồ sơ 2 GCN hợp lệ, còn nguyên nhân thật đã chặn ở prompt classify_page.)

    # Lỗi 2: KHÔNG có bìa nào trong cả file → mọi trang nội dung bị bỏ. Từ khi
    # groups_from_roles gắn content-đứng-trước-bìa vào nhóm bìa đầu, đây là ca
    # DUY NHẤT còn rơi vào nhánh bỏ — và gần như luôn nghĩa là NHÃN BÌA BỊ SAI
    # (bìa thật bị hạ thành content/other), chứ không phải file thiếu bìa.
    if not any(r == "cover" for r in roles) and any(r == "content" for r in roles):
        out.append("LỖI 2 — CẢ FILE không có trang nào được nhận là bìa → bỏ hết "
                   f"{len([r for r in roles if r == 'content'])} trang nội dung. "
                   "Nhiều khả năng bìa thật bị gán nhầm content/other")

    if dropped and not out:
        out.append(f"Trang rớt {[i+1 for i in dropped]} — chưa khớp mẫu lỗi nào, xem tay")
    if not dropped and len(groups) == 1 and not out:
        out.append("Không thấy bất thường: 1 nhóm, không mất trang nội dung nào")
    return out


def _print_one(nhan: str, roles: list[str], saved: list[list[int]] | None) -> Counter:
    n = len(roles)
    groups = groups_from_roles(roles)
    covered = {i for g in groups for i in g}
    dropped = _dropped(roles, groups)

    print(f"\n╭─ {nhan}   ·   {n} trang")
    if n <= DETECT_MIN_PAGES:
        print(f"│  ≤{DETECT_MIN_PAGES} trang → pipeline coi là 1 GIẤY, KHÔNG gọi classify_page.")
        print("│  (nhãn dưới đây chỉ để tham khảo, prod không dùng)")
    for i, r in enumerate(roles):
        co = "   ← RỚT" if i in dropped else ("" if i in covered else "   (loại — đúng)")
        print(f"│   tr.{i+1:<3} {_MARK.get(r, r)}{co}")
    print(f"│  → {len(groups)} nhóm: " + " | ".join(str([j + 1 for j in g]) for g in groups))
    if dropped:
        print(f"│  → RỚT {len(dropped)}/{n} trang: {[i+1 for i in dropped]}")
    lech = saved is not None and saved != groups
    if saved is not None:
        cũ = " | ".join(str([j + 1 for j in g]) for g in saved) or "(không có)"
        print(f"│  đã lưu trong Mongo: {cũ}   "
              f"[{'*** LỆCH ***' if lech else 'KHỚP'} với chạy lại]")
    for d in _diagnose(roles, groups):
        print(f"│  ⚠ {d}")
    print("╰─")

    st = Counter()
    st["file"] += 1
    st["trang_rot"] += len(dropped)
    if dropped:
        st["file_rot_trang"] += 1
    if len(groups) > 1:
        st["file_nhieu_nhom"] += 1
    if lech:
        st["lech_voi_da_luu"] += 1
    for d in _diagnose(roles, groups):
        st[d.split(" —")[0]] += 1
    return st


async def _from_file(path: str, vlm: int) -> Counter:
    with open(path, "rb") as f:
        buf = io.BytesIO(f.read())
    images = await _render(buf)
    roles = await _roles_of(images, vlm)
    return _print_one(os.path.basename(path), roles, None)


async def _render(buf: io.BytesIO) -> list[str]:
    loop = asyncio.get_running_loop()
    render = functools.partial(
        pdf_to_corrected_images, dpi=RENDER_DPI, max_img_size=RENDER_MAX_SIZE
    )
    return await loop.run_in_executor(None, render, buf)


async def _from_mongo(gcn_id: str, vlm: int) -> Counter | None:
    doc = await gcns().find_one({"_id": gcn_id})
    if not doc:
        print(f"  không thấy hồ sơ {gcn_id}")
        return None
    key = doc.get("s3_key")
    if not key:
        print(f"  hồ sơ {gcn_id} không có s3_key")
        return None
    # get_pdf trả io.BytesIO sẵn; source_connection_id để trỏ đúng kho nguồn.
    buf = await storage.get_pdf(key, doc.get("source_connection_id"))
    images = await _render(buf)
    roles = await _roles_of(images, vlm)
    saved = [list(r.get("page_indices") or []) for r in (doc.get("extractions") or [])]
    return _print_one(f"{doc.get('filename', gcn_id)}  ({gcn_id})", roles, saved)


def _nghi_ngo(doc: dict) -> str:
    """Hồ sơ này CÓ DẤU HIỆU cắt sai không? — suy từ dữ liệu ĐÃ LƯU, không cần GPU.

    Trả chuỗi lý do (rỗng = không nghi). Đây là cùng bộ tiêu chí của Phase 0.1a:
    dùng ở đây để NHẮM MẪU vào ca bệnh thay vì sample mù.
    """
    pc = doc.get("page_count") or 0
    recs = doc.get("extractions") or []
    groups = [list(r.get("page_indices") or []) for r in recs if isinstance(r, dict)]
    groups = [g for g in groups if g]
    if pc <= DETECT_MIN_PAGES or not groups:
        return ""
    phu = {i for g in groups for i in g}
    ly_do = []
    # Trang thiếu Ở ĐUÔI file là BÌNH THƯỜNG — gần như hồ sơ nào cũng đính CCCD/
    # tờ khai phía sau. Chỉ trang thiếu nằm TRƯỚC hoặc XEN GIỮA vùng đã gom mới là
    # dấu hiệu bị vứt oan (xếp ngược / 'other' cắt nhóm). Tiêu chí cũ "thiếu bất kỳ
    # trang nào" gắn cờ gần như toàn bộ file → lấy mẫu vô dụng (quét 32 ra 30 nghi).
    dau, cuoi = min(phu), max(phu)
    ho = [i for i in range(dau, cuoi) if i not in phu]
    if dau > 0:
        ly_do.append(f"{dau} trang trước bìa bị bỏ")
    if ho:
        ly_do.append(f"{len(ho)} trang hở giữa")
    le = sum(1 for g in groups if len(g) == 1)
    if le and len(groups) > 1:
        ly_do.append(f"{le} nhóm chỉ 1 trang")
    return " · ".join(ly_do)


async def _lay_mau(n: int, doi_chung: bool) -> list[str]:
    """Lấy n hồ sơ >5 trang: nghi ngờ (mặc định) hoặc BÌNH THƯỜNG (đối chứng).

    Đối chứng để chắc prompt mới không làm hỏng ca vốn đang đúng.
    """
    q = {"page_count": {"$gt": DETECT_MIN_PAGES}, "extractions": {"$exists": True, "$ne": []}}
    cur = gcns().find(q, {"page_count": 1, "extractions.page_indices": 1}).batch_size(500)
    ids, xet = [], 0
    async for doc in cur:
        xet += 1
        co = bool(_nghi_ngo(doc))
        if co != doi_chung:
            ids.append(doc["_id"])
            if len(ids) >= n:
                break
        if xet >= 20000:  # trần quét để không chờ mãi khi tỉ lệ hiếm
            break
    nhan = "BÌNH THƯỜNG (đối chứng)" if doi_chung else "NGHI cắt sai"
    print(f"  lấy mẫu: {len(ids)} hồ sơ {nhan} (quét {xet:,} hồ sơ >{DETECT_MIN_PAGES} trang)")
    return ids


async def main() -> None:
    p = argparse.ArgumentParser(description="Phase 0.1b — soi chuỗi nhãn classify_page.")
    p.add_argument("--file", default=None, help="PDF local trong container.")
    p.add_argument("--gcn-id", action="append", default=[], help="Hồ sơ trong Mongo (lặp được).")
    p.add_argument("--sph", action="append", default=[],
                   help="Tra theo Số phát hành in trên bìa (lặp được) — khỏi cần biết id.")
    p.add_argument("--ten", action="append", default=[],
                   help="Tra theo TÊN TỆP gốc, khớp một phần (lặp được). VD: --ten 0161849")
    p.add_argument("--ids-file", default=None, help="Tệp mỗi dòng 1 gcn_id.")
    p.add_argument("--mau", type=int, default=0,
                   help="Lấy N hồ sơ >5 trang CÓ DẤU HIỆU cắt sai (nhắm ca bệnh).")
    p.add_argument("--doi-chung", action="store_true",
                   help="Đổi --mau sang hồ sơ BÌNH THƯỜNG — chắc prompt mới không phá ca đang đúng.")
    p.add_argument("--limit", type=int, default=0, help="Trần số hồ sơ từ --ids-file.")
    p.add_argument("--vlm", type=int, default=8, help="Trần call classify_page đồng thời.")
    args = p.parse_args()

    ids = list(args.gcn_id)
    for ten in args.ten:
        # Khớp một phần trên filename VÀ s3_key (tên tệp gốc có thể chỉ còn trong key).
        # re.escape: tên tệp hay có dấu chấm/ngoặc → tránh biến thành metachar regex.
        rx = {"$regex": re.escape(ten), "$options": "i"}
        found = await gcns().find(
            {"$or": [{"filename": rx}, {"s3_key": rx}]},
            {"_id": 1, "filename": 1},
        ).to_list(20)
        if not found:
            print(f"  không thấy hồ sơ nào có tên tệp chứa {ten!r}")
        for d in found:
            print(f"  ↳ khớp {ten!r}: {d.get('filename')}  ({d['_id']})")
        ids += [d["_id"] for d in found]
    for sph in args.sph:
        # `extracted_so_phat_hanhs` đã được index sẵn (db.py) → tra nhanh, không quét.
        found = await gcns().find({"extracted_so_phat_hanhs": sph}, {"_id": 1}).to_list(20)
        if not found:
            print(f"  không thấy hồ sơ nào có Số phát hành {sph!r}")
        ids += [d["_id"] for d in found]
    if args.ids_file:
        with open(args.ids_file) as f:
            ids += [ln.strip() for ln in f if ln.strip()]
    if args.limit:
        ids = ids[: args.limit]
    if not args.file and not ids and not (args.sph or args.ten or args.mau):
        p.error("cần --file hoặc --ten/--sph/--gcn-id/--ids-file/--mau")

    print(f"render dpi={RENDER_DPI} max={RENDER_MAX_SIZE} (ĐÚNG production) · "
          f"DETECT_MIN_PAGES={DETECT_MIN_PAGES} · ≤{args.vlm} call đồng thời")
    if not args.file:
        print(f"DB: {config.MONGO_URI}/{config.MONGO_DB}")

    if args.mau:
        ids += await _lay_mau(args.mau, args.doi_chung)

    st = Counter()
    if args.file:
        st += await _from_file(args.file, args.vlm)
    for i in ids:
        r = await _from_mongo(i, args.vlm)
        if r:
            st += r

    if st["file"] > 1:
        print("\n" + "=" * 68)
        print(f" {st['file']} hồ sơ · {st['file_rot_trang']} hồ sơ RỚT TRANG "
              f"({st['trang_rot']} trang) · {st['file_nhieu_nhom']} hồ sơ >1 nhóm"
              f" · {st['lech_voi_da_luu']} hồ sơ LỆCH với bản đã lưu")
        for k, v in st.most_common():
            if k.startswith(("LỖI", "NGHI")):
                print(f"   {k:<16} {v}")
        print("=" * 68)


if __name__ == "__main__":
    asyncio.run(main())
