"""Soi 1 hồ sơ gcn: in chu_cuoi (tính lại) + kéo PDF GỐC từ MinIO ra file để đối chiếu.

Dùng khi rà kết quả backfill chu_cuoi — xem chủ cuối bóc ra có khớp giấy thật không.
CHỈ ĐỌC (kho nguồn read-only), không ghi gì.

Chạy TRONG CONTAINER (PDF lưu trong container → docker compose cp ra host để mở):
    docker compose exec api python -m app.scripts.soi_gcn <gcn_id> [<gcn_id> ...]
    docker compose exec api python -m app.scripts.soi_gcn <gcn_id> --out /data/soi
    # rồi kéo ra máy:
    docker compose cp api:/data/soi/. ./soi/
"""

import argparse
import asyncio
import os

from app import config, storage
from app.db import gcns
from src.extentions.multimodal.chu_cuoi import chu_cuoi_for_result


def _print_chu_cuoi(doc: dict) -> None:
    print(f"\n╭─ {doc.get('_id')}  ·  {doc.get('filename', '?')}")
    print(f"│  trang {doc.get('page_count', '?')}  ·  s3_key {doc.get('s3_key', '?')}")
    idx = 0
    for rec in doc.get("extractions") or []:
        res = rec.get("result") if isinstance(rec, dict) else None
        if not isinstance(res, dict):
            continue
        for cc in chu_cuoi_for_result(res):
            idx += 1
            src = "BIẾN ĐỘNG" if cc["nguon"] == "bien_dong" else "giấy gốc"
            print(f"│  [{idx}] nguồn={src}  tin={cc['confidence']}"
                  + (f"  ({cc['thoi_gian']})" if cc.get("thoi_gian") else ""))
            if cc.get("canh_bao"):
                print("│      ⚠ CÓ CHUYỂN NHƯỢNG NHƯNG CHƯA RÕ CHỦ — cần xác minh")
            for c in cc["chu"] or [{"Tên chủ": "(chưa rõ)"}]:
                extra = " · ".join(x for x in [
                    c.get("Loại giấy tờ", ""), c.get("Số giấy tờ", ""),
                    c.get("Năm sinh", ""), c.get("Địa chỉ", "")] if x)
                print(f"│      • {c.get('Tên chủ', '')}" + (f"   ({extra})" if extra else ""))
    # cũng in chu_cuoi đã LƯU (nếu có) để so với bản tính lại
    saved = doc.get("chu_cuoi")
    if saved:
        print(f"│  (đã lưu: {len(saved)} entry, version {doc.get('chu_cuoi_version')})")
    print("╰─")


async def _pull_pdf(doc: dict, out_dir: str) -> None:
    key = doc.get("s3_key")
    if not key:
        print(f"   ⚠ {doc['_id']}: không có s3_key")
        return
    try:
        buf = await storage.get_pdf(key, doc.get("source_connection_id"))
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠ {doc['_id']}: không tải được PDF gốc: {e}")
        return
    os.makedirs(out_dir, exist_ok=True)
    name = doc.get("filename") or f"{doc['_id']}.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    path = os.path.join(out_dir, f"{doc['_id']}__{os.path.basename(name)}")
    with open(path, "wb") as f:
        f.write(buf.getvalue())
    print(f"   ↓ PDF gốc: {path}")


async def main() -> None:
    p = argparse.ArgumentParser(description="Soi chu_cuoi + kéo PDF gốc để đối chiếu.")
    p.add_argument("gcn_ids", nargs="+", help="Một hoặc nhiều _id hồ sơ gcn.")
    p.add_argument("--out", default="/data/soi", help="Thư mục lưu PDF (trong container).")
    p.add_argument("--no-pdf", action="store_true", help="Chỉ in chu_cuoi, không kéo PDF.")
    args = p.parse_args()

    print(f"DB: {config.MONGO_URI}/{config.MONGO_DB}")
    for gid in args.gcn_ids:
        doc = await gcns().find_one({"_id": gid})
        if not doc:
            print(f"\n⚠ Không tìm thấy hồ sơ {gid}")
            continue
        _print_chu_cuoi(doc)
        if not args.no_pdf:
            await _pull_pdf(doc, args.out)


if __name__ == "__main__":
    asyncio.run(main())
