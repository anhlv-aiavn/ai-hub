"""Phase 0.1b — soi CHUỖI NHÃN từng trang để biết vì sao file bị cắt sai.

`_groups_from_roles` chỉ là hệ quả; nguyên nhân nằm ở nhãn `classify_page` trả về
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
    docker compose exec api python -m app.scripts.audit_cut_vlm --sph "D 0161849"
    docker compose exec api python -m app.scripts.audit_cut_vlm --gcn-id <id>

    # soi cả danh sách nghi ngờ (mỗi dòng 1 gcn_id, do audit_cut_offline xuất)
    docker compose exec api python -m app.scripts.audit_cut_vlm --ids-file /tmp/nghi.txt --limit 100
"""

import argparse
import asyncio
import functools
import io
import os
from collections import Counter

from app import config, storage
from app.db import gcns
from app.scripts.bench_pipeline import _groups_from_roles
from src.extentions.multimodal.detect_gcn import classify_page
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

    # Cờ MỀM — nhóm ngắn bám đuôi file. Có thể là CCCD/CMND kèm theo bị đọc thành
    # bìa (tie-break "có Số phát hành" quá lỏng), NHƯNG cũng có thể là GCN thứ 2
    # hợp lệ. Chuỗi nhãn không phân biệt được → chỉ nêu để người soi, không kết luận.
    if len(groups) > 1:
        cuoi = groups[-1]
        if len(cuoi) <= 2 and cuoi[-1] == n - 1:
            out.append(f"XEM LẠI — nhóm cuối chỉ {len(cuoi)} trang {[i+1 for i in cuoi]} bám "
                       f"đuôi file: là GCN thứ 2 thật, hay CCCD/giấy tờ kèm bị đọc thành bìa?")

    # Lỗi 2: content lạc trước khi có bìa nào.
    truoc = [i for i in dropped if roles[i] == "content" and not any(
        roles[j] == "cover" for j in range(i))]
    if truoc:
        out.append(f"LỖI 2 — trang {[i+1 for i in truoc]} là 'content' nhưng CHƯA có bìa nào "
                   f"đứng trước → bị bỏ IM LẶNG")

    if dropped and not out:
        out.append(f"Trang rớt {[i+1 for i in dropped]} — chưa khớp mẫu lỗi nào, xem tay")
    if not dropped and len(groups) == 1 and not out:
        out.append("Không thấy bất thường: 1 nhóm, không mất trang nội dung nào")
    return out


def _print_one(nhan: str, roles: list[str], saved: list[list[int]] | None) -> Counter:
    n = len(roles)
    groups = _groups_from_roles(roles)
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
    if saved is not None:
        cũ = " | ".join(str([j + 1 for j in g]) for g in saved) or "(không có)"
        khớp = "KHỚP" if saved == groups else "*** LỆCH ***"
        print(f"│  đã lưu trong Mongo: {cũ}   [{khớp} với chạy lại]")
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


async def main() -> None:
    p = argparse.ArgumentParser(description="Phase 0.1b — soi chuỗi nhãn classify_page.")
    p.add_argument("--file", default=None, help="PDF local trong container.")
    p.add_argument("--gcn-id", action="append", default=[], help="Hồ sơ trong Mongo (lặp được).")
    p.add_argument("--sph", action="append", default=[],
                   help="Tra theo Số phát hành in trên bìa (lặp được) — khỏi cần biết id.")
    p.add_argument("--ids-file", default=None, help="Tệp mỗi dòng 1 gcn_id.")
    p.add_argument("--limit", type=int, default=0, help="Trần số hồ sơ từ --ids-file.")
    p.add_argument("--vlm", type=int, default=8, help="Trần call classify_page đồng thời.")
    args = p.parse_args()

    ids = list(args.gcn_id)
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
    if not args.file and not ids and not args.sph:
        p.error("cần --file hoặc --sph/--gcn-id/--ids-file")

    print(f"render dpi={RENDER_DPI} max={RENDER_MAX_SIZE} (ĐÚNG production) · "
          f"DETECT_MIN_PAGES={DETECT_MIN_PAGES} · ≤{args.vlm} call đồng thời")
    if not args.file:
        print(f"DB: {config.MONGO_URI}/{config.MONGO_DB}")

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
              f"({st['trang_rot']} trang) · {st['file_nhieu_nhom']} hồ sơ >1 nhóm")
        for k, v in st.most_common():
            if k.startswith(("LỖI", "NGHI")):
                print(f"   {k:<16} {v}")
        print("=" * 68)


if __name__ == "__main__":
    asyncio.run(main())
