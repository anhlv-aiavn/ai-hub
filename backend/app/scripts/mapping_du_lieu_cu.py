"""Đối chiếu tên tệp gốc (`filename`) của các hồ sơ ĐÃ CÓ SẴN trong `db.gcn`
với cache liệt kê (các) kho MinIO/S3 nguồn — KHÔNG tự liệt kê MinIO (đọc cache
do `app.scripts.quet_minio_ra_cache` tạo sẵn). Dùng để mapping dữ liệu CŨ
(backfill) 1 lần hoặc định kỳ; hồ sơ MỚI sau này sẽ mapping theo cách khác
(chưa làm ở script này — xem ghi chú cuối docstring).

Quy tắc chuẩn hoá tên, lọc từ khoá "GT"/"GCN", loại lô test "Lô dd/mm HH:MM" —
xem `app/scripts/matching_core.py` (dùng chung với `quet_minio_ra_cache.py`).

CÁCH TÍNH SỐ LƯỢNG KHỚP (2 con số, ý nghĩa khác nhau):
  - "chưa tính trùng": đếm theo TỪNG hồ sơ trong `db.gcn` — kể cả nhiều hồ sơ
    trùng tên nhau vẫn tính riêng từng cái, miễn tên chuẩn hoá có khớp.
  - "đã tính trùng": đếm theo NHÓM tên chuẩn hoá phân biệt ở phía ai-hub (nhiều
    hồ sơ cùng tên chuẩn hoá → chỉ tính 1 nhóm).

Chạy TRONG CONTAINER (dùng thẳng kết nối Mongo của app — không cần tự truyền
URI):
    docker compose exec api python -m app.scripts.mapping_du_lieu_cu                # dry-run
    docker compose exec api python -m app.scripts.mapping_du_lieu_cu --write        # ghi vào Mongo
    docker compose exec api python -m app.scripts.mapping_du_lieu_cu --limit 5000    # chạy thử

Khi `--write`: với mỗi hồ sơ tìm được ≥ 1 file khớp (ở bất kỳ nguồn nào trong
số các `--source-connection-id` đã đối chiếu), script thêm field
`s3_key_mapping` vào đúng document đó — LUÔN LÀ MẢNG (kể cả khi chỉ khớp đúng
1 file, để đồng nhất cấu trúc cho giao diện xử lý sau này):
    s3_key_mapping: [
        {
            source_connection_id: <_id trong s3_connections>,
            display_name:         <tên file để hiển thị trên UI>,
            filepath:             <object key trong kho nguồn — dùng để mở file>,
            matched_name:         <chuỗi tên đã chuẩn hoá dùng để khớp>,
            matched_at:           <thời điểm chạy, UTC>,
        },
        ...  # nhiều phần tử nếu tên chuẩn hoá khớp NHIỀU file (mập mờ, hoặc
             # khớp ở nhiều nguồn khác nhau)
    ]
Cách ĐẾM số liệu "mập mờ" (nhóm khớp ≥ 2 file) trong báo cáo KHÔNG đổi — nhóm
mập mờ vẫn được GHI, dạng mảng nhiều phần tử (không bỏ qua). Field này không
đổi cấu trúc field nào khác đang có trong `gcn`.

Các lô có tên TỰ SINH dạng "Lô dd/mm HH:MM" (không đặt tên riêng lúc tạo —
thường là lô test) bị LOẠI khỏi phạm vi đối chiếu.

GHI CHÚ: script này chỉ xử lý hồ sơ ĐÃ CÓ trong Mongo tại thời điểm chạy. Việc
tự động mapping cho hồ sơ MỚI ngay khi xử lý xong (nối vào worker) và hiển thị
cột trên giao diện CHƯA làm ở đợt này — sẽ làm sau, tránh ảnh hưởng luồng
worker đang chạy thật.

Kết quả ghi vào `--out-dir` (mặc định thư mục cạnh script, NẰM TRONG image
container — cần mount volume hoặc `docker cp` ra ngoài để lấy về):
  - mapping_du_lieu_cu_<timestamp>_tom_tat.txt          số liệu tổng hợp
  - mapping_du_lieu_cu_<timestamp>_part001.csv, ...      danh sách chi tiết
    (chia nhiều file, mỗi file tối đa ROWS_PER_CSV dòng, vì tổng > 1 triệu bản ghi)
"""

import argparse
import asyncio
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

from pymongo import UpdateOne

from app.db import batches, gcns
from app.scripts import matching_core as mc

# Console Windows mặc định dùng codepage cp1258/cp850, không encode được hết
# tiếng Việt -> ép stdout/stderr sang UTF-8 để tránh crash khi chạy thử trực
# tiếp ngoài container (trong container Linux/UTF-8 thì đoạn này vô hại).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CACHE_DIR = os.path.join(SCRIPT_DIR, "cache_minio_index")
ROWS_PER_CSV = 500_000  # chia file CSV chi tiết, mỗi file tối đa từng này dòng


class CsvPartWriter:
    """Tự chia file CSV chi tiết thành nhiều phần, mỗi phần tối đa `rows_per_file` dòng."""

    def __init__(self, out_dir: str, ts: str, rows_per_file: int):
        self._out_dir = out_dir
        self._ts = ts
        self._rows_per_file = rows_per_file
        self._part = 0
        self._rows_in_part = 0
        self._f = None
        self._w = None
        self.paths: list[str] = []
        self._header = ["stt", "gcn_id", "filename_aihub", "ten_chuan_hoa",
                        "so_ban_ghi_cung_ten_aihub", "tim_thay",
                        "so_luong_khop", "ung_vien (source_connection_id:key;...)",
                        "da_ghi_vao_mongo"]
        self._open_new_part()

    def _open_new_part(self):
        if self._f:
            self._f.close()
        self._part += 1
        self._rows_in_part = 0
        path = os.path.join(self._out_dir, f"mapping_du_lieu_cu_{self._ts}_part{self._part:03d}.csv")
        self._f = open(path, "w", newline="", encoding="utf-8-sig")
        self._w = csv.writer(self._f)
        self._w.writerow(self._header)
        self.paths.append(path)

    def write_row(self, row: list):
        if self._rows_in_part >= self._rows_per_file:
            self._open_new_part()
        self._w.writerow(row)
        self._rows_in_part += 1

    def close(self):
        if self._f:
            self._f.close()


async def scan_mongo_groups(excluded_batch_ids: set[str]) -> tuple[dict[str, list[dict]], int, int]:
    """Duyệt `db.gcn` (trừ các lô trong `excluded_batch_ids`), gom theo tên
    chuẩn hoá. Trả về (groups, tong_so_ho_so_co_filename, so_ho_so_khong_co_filename)."""
    groups: dict[str, list[dict]] = defaultdict(list)
    no_filename = 0
    total_with_name = 0
    flt = {"batch_id": {"$nin": list(excluded_batch_ids)}} if excluded_batch_ids else {}
    i = 0
    async for doc in gcns().find(flt, {"filename": 1}):
        i += 1
        fname = doc.get("filename")
        if not fname:
            no_filename += 1
        else:
            groups[mc.normalize_filename(fname)].append({"_id": doc["_id"], "filename": fname})
            total_with_name += 1
        if i % 200_000 == 0:
            print(f"  ... đã quét {i:,} hồ sơ trong ai-hub")
    return dict(groups), total_with_name, no_filename


async def run(args: argparse.Namespace) -> None:
    source_ids = args.source_connection_ids or [mc.DEFAULT_SOURCE_CONNECTION_ID]

    excluded_batch_ids = await mc.find_test_batch_ids(batches())
    print(f"Số lô tên tự sinh 'Lô dd/mm HH:MM' (loại khỏi đối chiếu): {len(excluded_batch_ids):,}")

    # ── Đọc cache của (các) nguồn — KHÔNG gọi MinIO ─────────────────────────
    print(f"Đọc cache cho {len(source_ids)} nguồn: {source_ids}")
    combined: dict[str, list[mc.Candidate]] = defaultdict(list)
    source_stats: list[tuple[str, int, int]] = []
    for source_id in source_ids:
        idx, total = mc.load_merged_index(args.cache_dir, source_id)
        if not idx:
            print(f"!! CẢNH BÁO: nguồn '{source_id}' chưa có cache (chưa chạy "
                  f"app.scripts.quet_minio_ra_cache --source-connection-id {source_id} lần nào?) "
                  f"-> bỏ qua", file=sys.stderr)
        for norm, keys in idx.items():
            combined[norm].extend((source_id, key) for key in keys)
        source_stats.append((source_id, total, len(idx)))
        print(f"  Nguồn '{source_id}': {total:,} object, {len(idx):,} tên chuẩn hoá")

    minio_names_raw = len(combined)
    minio_objects_raw = sum(len(v) for v in combined.values())
    minio_index = mc.apply_keyword_filter(dict(combined), args.minio_keyword_filter)
    minio_names_filtered = len(minio_index)
    minio_objects_filtered = sum(len(v) for v in minio_index.values())
    print(f"Lọc theo từ khoá ('{args.minio_keyword_filter}'): còn {minio_names_filtered:,} tên chuẩn hoá, "
         f"{minio_objects_filtered:,}/{minio_objects_raw:,} object làm ứng viên đối chiếu")

    print("Đang duyệt db.gcn...")
    groups, total_with_name, no_filename = await scan_mongo_groups(excluded_batch_ids)
    print(f"Tổng số hồ sơ có filename trong ai-hub: {total_with_name:,}")
    print(f"Số hồ sơ không có filename: {no_filename:,}")
    print(f"Số nhóm tên chuẩn hoá phân biệt trong ai-hub: {len(groups):,}")

    # ── Đối chiếu ─────────────────────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_writer = CsvPartWriter(args.out_dir, ts, ROWS_PER_CSV)

    matched_docs_raw = 0          # chưa tính trùng: đếm theo từng hồ sơ
    unmatched_docs_raw = 0
    matched_groups = 0            # đã tính trùng: đếm theo nhóm tên chuẩn hoá
    unmatched_groups = 0
    matched_source_keys: set[mc.Candidate] = set()  # (nguồn, key) riêng biệt đã dùng làm kết quả khớp
    ambiguous_groups = 0          # nhóm khớp NHIỀU hơn 1 file -> vẫn ghi, dạng mảng nhiều phần tử
    updates: list[UpdateOne] = []
    written_ok = 0
    written_err = 0
    stt = 0
    processed_docs = 0
    now = datetime.now(timezone.utc)

    async def flush_updates(force: bool = False):
        nonlocal updates, written_ok, written_err
        if not updates or (len(updates) < 1000 and not force):
            return
        try:
            res = await gcns().bulk_write(updates, ordered=False)
            written_ok += res.modified_count
        except Exception as e:  # noqa: BLE001
            print(f"!! Lỗi ghi Mongo (batch {len(updates)} bản ghi): {e}", file=sys.stderr)
            written_err += len(updates)
        updates = []

    for norm, items in groups.items():
        candidates = minio_index.get(norm) or []
        found = bool(candidates)
        unique = len(candidates) == 1

        if found:
            matched_groups += 1
            matched_source_keys.update(candidates)
            if not unique:
                ambiguous_groups += 1
        else:
            unmatched_groups += 1

        for it in items:
            stt += 1
            processed_docs += 1
            if args.limit and processed_docs > args.limit:
                break
            if found:
                matched_docs_raw += 1
            else:
                unmatched_docs_raw += 1

            # Không đòi hỏi `unique` — nhóm mập mờ (≥2 file khớp) vẫn ghi, chỉ
            # khác là mảng s3_key_mapping có nhiều hơn 1 phần tử.
            will_write = (not args.dry_run) and found
            csv_writer.write_row([
                stt, str(it["_id"]), it["filename"], norm, len(items),
                "co" if found else "khong",
                len(candidates),
                ";".join(f"{sid}:{key}" for sid, key in candidates),
                "co" if will_write else "khong",
            ])

            if will_write:
                updates.append(UpdateOne(
                    {"_id": it["_id"]},
                    {"$set": {
                        mc.S3_KEY_MAPPING_FIELD: [
                            {
                                "source_connection_id": sid,
                                "display_name": key.rsplit("/", 1)[-1],
                                "filepath": key,
                                "matched_name": norm,
                                "matched_at": now,
                            }
                            for sid, key in candidates
                        ],
                    }},
                ))
                await flush_updates()
        if args.limit and processed_docs > args.limit:
            break

    await flush_updates(force=True)
    csv_writer.close()

    # ── Tổng hợp ──────────────────────────────────────────────────────────
    summary_path = os.path.join(args.out_dir, f"mapping_du_lieu_cu_{ts}_tom_tat.txt")
    lines = [
        f"Thời điểm chạy: {datetime.now().isoformat()}",
        f"Chế độ: {'DRY-RUN (khong ghi Mongo)' if args.dry_run else 'GHI VAO MONGO (--write)'}",
        f"Gioi han (--limit): {args.limit if args.limit else 'khong gioi han'}",
        "",
        f"--- Nguon doi chieu (cache_dir={args.cache_dir}) ---",
    ] + [f"  '{sid}': {total:,} object, {n_names:,} ten chuan hoa" for sid, total, n_names in source_stats] + [
        f"Tong cong: {minio_objects_raw:,} object, {minio_names_raw:,} ten chuan hoa (truoc khi loc tu khoa)",
        "",
        f"Loc tu khoa ten (--minio-keyword-filter): {args.minio_keyword_filter}",
        f"So ten chuan hoa con lai sau loc: {minio_names_filtered:,}",
        f"So object con lai lam ung vien doi chieu: {minio_objects_filtered:,} / {minio_objects_raw:,}",
        "",
        f"--- db.gcn (ai-hub) ---",
        f"So lo ten tu sinh 'Lo dd/mm HH:MM' bi loai khoi doi chieu: {len(excluded_batch_ids):,}",
        f"Tong so ho so co filename: {total_with_name:,}",
        f"So ho so khong co filename: {no_filename:,}",
        f"So nhom ten chuan hoa rieng biet (da tinh trung): {len(groups):,}",
        "",
        f"--- Ket qua doi chieu ---",
        f"So ho so CHUA TINH TRUNG tim thay file khop: {matched_docs_raw:,}",
        f"So ho so CHUA TINH TRUNG KHONG tim thay: {unmatched_docs_raw:,}",
        f"So nhom ten DA TINH TRUNG (gop theo ten chuan hoa) tim thay file khop: {matched_groups:,}",
        f"So nhom ten DA TINH TRUNG KHONG tim thay: {unmatched_groups:,}",
        f"So (nguon, file) rieng biet da duoc dung lam ket qua khop: {len(matched_source_keys):,}",
        f"So nhom ten khop VOI NHIEU HON 1 file (mo ho -> van ghi, dang mang nhieu phan tu): {ambiguous_groups:,}",
    ]
    if not args.dry_run:
        lines += [
            "",
            f"--- Ghi vao Mongo ---",
            f"So document da cap nhat field '{mc.S3_KEY_MAPPING_FIELD}' thanh cong: {written_ok:,}",
            f"So document loi khi ghi: {written_err:,}",
        ]
    lines += ["", "File chi tiet (moi hang = 1 ho so ai-hub):"] + [f"  {path}" for path in csv_writer.paths]

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("\n" + "\n".join(lines))
    print(f"\nDa ghi tom tat: {summary_path}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR,
                   help="Thư mục gốc chứa cache do quet_minio_ra_cache.py tạo (mặc định "
                        "app/scripts/cache_minio_index/ cạnh script).")
    p.add_argument("--source-connection-id", action="append", dest="source_connection_ids",
                   help=f"Nguồn cần đối chiếu (đọc cache của nguồn đó) — truyền nhiều lần để đối "
                        f"chiếu cùng lúc với nhiều nguồn. Mặc định: chỉ connection 'hsq-hni' "
                        f"({mc.DEFAULT_SOURCE_CONNECTION_ID}).")
    p.add_argument("--out-dir", default=SCRIPT_DIR, help="Thư mục ghi kết quả (mặc định: thư mục chứa script).")
    p.add_argument("--limit", type=int, default=None,
                   help="Chỉ xử lý N hồ sơ ĐẦU TIÊN quét được (phục vụ chạy thử). Mặc định: toàn bộ.")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                   help="(Mặc định) Chỉ đối chiếu + xuất báo cáo, KHÔNG ghi gì vào Mongo.")
    p.add_argument("--write", dest="dry_run", action="store_false",
                   help="Ghi field 's3_key_mapping' (kết quả khớp) vào từng document Mongo tương ứng. Tắt dry-run.")
    p.add_argument("--minio-keyword-filter", choices=mc.MINIO_KEYWORD_FILTER_CHOICES, default="khong-loc",
                   help="Cách lọc file nguồn theo từ khoá trong tên TRƯỚC khi đối chiếu (chỉ chọn 1): "
                        "'khong-loc' = đối chiếu TẤT CẢ file (mặc định); "
                        "'loai-gt' = bỏ file có từ khoá 'GT'; "
                        "'chi-gcn' = CHỈ đối chiếu file có từ khoá 'GCN'.")
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
