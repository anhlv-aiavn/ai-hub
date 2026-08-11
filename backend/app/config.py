"""Cấu hình AI-HUB — đọc từ env. Dùng chung tên biến với engine GCN vendor
(MONGO_*, *_MINIO, VLLM_*) để minio_helper/mongo_helper/vlm_client chạy nguyên trạng."""

import os


def _b(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# ── MongoDB (kho riêng AI-HUB) ──────────────────────────────────────────────
MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo:27017")
MONGO_DB = os.getenv("MONGO_DB", "aihub")
COLL_BATCH = "batch"
COLL_GCN = "gcn"
COLL_USER = "user"
COLL_SITE_CONFIG = "site_config"
COLL_S3_CONN = "s3_connections"
COLL_AUDIT = "audit_log"
COLL_IMPORT_JOB = "import_jobs"
COLL_ACCESS_LOG = "access_log"
COLL_EXPORT_JOB = "export_jobs"
COLL_BROWSE_PROGRESS = "browse_progress_cache"
COLL_QC_SYNC_CONFIG = "qc_sync_configs"
COLL_QC_SYNC_JOB = "qc_sync_jobs"
COLL_QC_ITEM = "qc_items"
COLL_QC_STATS_DAILY = "qc_stats_daily"

# Hậu kiểm: TTL soft-lock (giữ chỗ khi đang sửa, tránh 2 người ghi đè nhau).
REVIEW_LOCK_TTL = int(os.getenv("AIHUB_REVIEW_LOCK_TTL", "300"))

# ── MinIO (kho riêng AI-HUB) — biến ENDPOINT_URL_MINIO/… do minio_helper đọc ─
AIHUB_BUCKET = os.getenv("AIHUB_BUCKET", "ai-hub")

# ── MinIO client pool (PERF-1 — xem docs/features_issues.md#perf-minio-pool) ──
# Tái dùng 1 client aioboto3 SỐNG LÂU cho mỗi endpoint thay vì tạo Session+client
# mới mỗi call (mỗi call cũ = 1 bắt tay TCP/TLS mới → nghẽn hot path ingest).
# KILL-SWITCH: đặt AIHUB_S3_POOL=false để quay lại hành vi cũ (per-call client)
# NGAY, không cần đổi code — dùng khi nghi client pool gây sự cố trên production.
S3_POOL_ENABLED = _b("AIHUB_S3_POOL", "true")
# Trần connection trong pool của 1 client — nên ≥ số S3-op đồng thời (~MAX_IN_FLIGHT
# + số cut ghi song song). Mặc định boto3 chỉ 10 → phải nới.
S3_POOL_MAX_CONNECTIONS = int(os.getenv("AIHUB_S3_POOL_MAX_CONNECTIONS", "128"))

# ── Redis (bus SSE) ─────────────────────────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
EVENT_CHANNEL = os.getenv("EVENT_CHANNEL", "aihub:events")

# ── Worker concurrency (in-flight batching cho vLLM) ────────────────────────
# Số call VLM đồng thời tối đa (detect+extract) — bơm để vLLM dynamic-batch.
MAX_VLM_CONCURRENT = int(os.getenv("MAX_VLM_CONCURRENT", "8"))
# Số file in-flight tối đa (bound RAM ảnh render).
MAX_IN_FLIGHT = int(os.getenv("MAX_IN_FLIGHT", str(MAX_VLM_CONCURRENT * 3)))
# Tách file PDF per-GCN (cut-*.pdf) ghi lên S3 ĐÍCH. Tắt (false) khi không cần
# file cắt — bỏ hẳn tải ghi hàng loạt lên đích (thủ phạm 502 khi đích là cổng
# public quá tải). Dữ liệu trích xuất KHÔNG phụ thuộc cut; tắt chỉ mất file PDF
# tách + cột "tệp cắt". Bật lại lúc nào cũng được, không mất dữ liệu.
BUILD_CUTS = _b("AIHUB_BUILD_CUTS", "true")

# Đo thời gian TỪNG CHẶNG mỗi hồ sơ (download S3 / pipeline render+VLM / ghi cut)
# và ghi vào `gcn.timings` — để `app.scripts.watch` bóc tách nút thắt LÚC ĐANG
# CHẠY. Chi phí tốc độ ~0 (vài time.monotonic + gộp dict vào update đã có); chỉ
# tốn thêm ít storage/doc. Mặc định BẬT để luôn có dữ liệu quan sát; tắt bằng
# AIHUB_TRACE_TIMINGS=false nếu muốn doc gọn.
TRACE_TIMINGS = _b("AIHUB_TRACE_TIMINGS", "true")

# ── Dry-run (tích hợp riêng ngoài hệ thống chính) ───────────────────────────
# File tạm lên BUCKET RIÊNG (khác AIHUB_BUCKET của hệ thống thật) — xoá ngay
# sau khi xử lý xong; lifecycle rule bên dưới là lưới an toàn nếu tiến trình
# chết giữa chừng trước khi kịp xoá tay. Kết quả (JSON) + trạng thái nằm ở
# collection riêng `dryrun_job`/`dryrun_item`, KHÔNG đụng `gcn`/`batch` — tự
# xoá sau TTL này (giây).
DRYRUN_BUCKET = os.getenv("AIHUB_DRYRUN_BUCKET", "ai-hub-dryrun-tmp")
DRYRUN_S3_LIFECYCLE_DAYS = int(os.getenv("AIHUB_DRYRUN_S3_LIFECYCLE_DAYS", "1"))
COLL_DRYRUN_JOB = "dryrun_job"
COLL_DRYRUN_ITEM = "dryrun_item"
DRYRUN_TTL_SECONDS = int(os.getenv("AIHUB_DRYRUN_TTL_SECONDS", str(2 * 3600)))
# Số file dry-run xử lý ĐỒNG THỜI tối đa (toàn API process, mọi job cộng lại)
# — tách riêng khỏi MAX_VLM_CONCURRENT của worker chính để không giành tải VLM
# với hệ thống thật đang chạy.
DRYRUN_MAX_VLM_CONCURRENT = int(os.getenv("AIHUB_DRYRUN_MAX_VLM_CONCURRENT", "8"))

# ── Auth tùy chọn (cũ): nếu set → bắt buộc X-API-Key. Giữ cho tương thích. ────
API_KEY = os.getenv("AIHUB_API_KEY", "").strip()

# ── Tài khoản (JWT) ─────────────────────────────────────────────────────────
# Secret ký JWT — BẮT BUỘC đặt ở môi trường thật. Dev rỗng → khóa tạm (cảnh báo).
JWT_SECRET = os.getenv("AIHUB_JWT_SECRET", "").strip() or "dev-insecure-change-me"
JWT_TTL = int(os.getenv("AIHUB_JWT_TTL_SECONDS", str(7 * 24 * 3600)))  # 7 ngày
# Seed admin đầu tiên lúc startup (nếu chưa có user nào).
ADMIN_USER = os.getenv("AIHUB_ADMIN_USER", "").strip()
ADMIN_PASS = os.getenv("AIHUB_ADMIN_PASS", "")
# Chống brute-force: khóa tạm sau N lần sai trong một cửa sổ.
LOGIN_MAX_FAILS = int(os.getenv("AIHUB_LOGIN_MAX_FAILS", "5"))
LOGIN_LOCK_SECONDS = int(os.getenv("AIHUB_LOGIN_LOCK_SECONDS", "300"))
# 1 tài khoản chỉ 1 phiên "đang hoạt động" tại 1 thời điểm. "Đang hoạt động" =
# có heartbeat (FE gọi mỗi TTL/3) trong TTL giây gần nhất. Đăng nhập mới khi
# phiên cũ còn trong TTL này bị từ chối (409) thay vì đá phiên cũ.
SESSION_ACTIVE_TTL = int(os.getenv("AIHUB_SESSION_TTL_SECONDS", "90"))
# Bật/tắt việc ép buộc 1 phiên/tài khoản (chặn login 409 + vô hiệu token phiên
# cũ). Mặc định TẮT (không set ENV = cho phép nhiều phiên song song) — tuỳ
# build/môi trường bật lên khi cần (vd môi trường nội bộ muốn khoá 1 phiên).
SINGLE_SESSION_ENABLED = _b("AIHUB_SINGLE_SESSION_ENABLED", "false")

# ── Đối soát: DPI/scale render ảnh trang ────────────────────────────────────
PAGE_RENDER_MAX_W = int(os.getenv("PAGE_RENDER_MAX_W", "2200"))

# ── Duyệt kho MinIO: đếm tiến độ import theo thư mục (§ badge "đã xử lý") ────
# Duyệt đệ quy để đếm x/y — giới hạn số file đếm để không treo UI với cây quá
# lớn; vượt ngưỡng thì trả capped=true, FE hiện "≥ N" thay vì số đếm chính xác.
BROWSE_PROGRESS_CAP = int(os.getenv("AIHUB_BROWSE_PROGRESS_CAP", "2000"))
# Cache phần ĐẮT (liệt kê đệ quy MinIO ra danh sách key) theo source+prefix, dùng
# chung cho MỌI người/phiên xem — phần "đã import bao nhiêu" luôn đếm lại tươi
# (rẻ, 1 query) nên số hóa xong là thấy đúng ngay, không cần invalidate cache.
# TTL để tự làm mới nếu nội dung kho nguồn đổi ngoài luồng của hệ thống.
BROWSE_PROGRESS_CACHE_TTL = int(os.getenv("AIHUB_BROWSE_PROGRESS_CACHE_TTL", str(3600)))

# ── QC Sync — kiểm chất lượng qua qc-scanner-server ngoài + OCR + crop GCN ──
# Pipeline MỚI, song song với pipeline GCN chính: đồng bộ 1 kho MinIO nguồn do
# người dùng cấu hình, chấm chất lượng scan qua service ngoài (docs/api.md),
# nếu đạt (pass/warn) thì chạy OCR (tái dùng _pipeline/_build_cuts có sẵn) rồi
# cắt trang GCN lưu vào MinIO đích RIÊNG (s3_connections purpose="qc").
QC_SCANNER_BASE_URL = os.getenv("QC_SCANNER_BASE_URL", "http://192.168.120.9:5000").rstrip("/")
QC_SCANNER_API_KEY = os.getenv("QC_SCANNER_API_KEY", "").strip()
QC_SCANNER_TIMEOUT = float(os.getenv("QC_SCANNER_TIMEOUT_SECONDS", "60"))
# Trần số request gửi ĐỒNG THỜI tới qc-scanner-server — RIÊNG, không dùng chung
# _VLM_SEM của pipeline GCN. Mặc định vừa phải; đọc `max_concurrency` ở
# GET /healthz của service thật để chỉnh cho khớp máy đích (đừng ghi cứng).
QC_SCANNER_MAX_CONCURRENT = int(os.getenv("QC_SCANNER_MAX_CONCURRENT", "8"))
# Retry cho mã DUY NHẤT nên retry theo hợp đồng API (503 SERVER_BUSY) — ảnh
# chưa được xử lý lần nào, không phải phán quyết về ảnh.
QC_SCANNER_RETRY_MAX = int(os.getenv("QC_SCANNER_RETRY_MAX", "3"))

# Chu kỳ mặc định (giây) worker tự quét lại 1 kênh đồng bộ để tìm file MỚI —
# mỗi lượt là full re-scan (S3 liệt kê theo thứ tự key, không theo mtime, nên
# không "resume" được giữa các chu kỳ); dedup rẻ nhờ unique index qc_items,
# không đọc trước khi ghi.
QC_SYNC_DEFAULT_INTERVAL_SECONDS = int(os.getenv("QC_SYNC_DEFAULT_INTERVAL_SECONDS", "300"))
WORKER_QC_SYNC_MAX_CONCURRENT = int(os.getenv("WORKER_QC_SYNC_MAX_CONCURRENT", "2"))
# LƯU Ý VẬN HÀNH: bước OCR của qc_item dùng CHUNG pool vLLM (_VLM_SEM) với
# pipeline GCN sản xuất — đây đang là nút thắt #1 của dự án
# (features_issues.md#bottleneck-vlm). Mặc định để THẤP, chỉ nới khi xác nhận
# đủ dư GPU cho cả 2 pipeline.
WORKER_QC_ITEM_MAX_CONCURRENT = int(os.getenv("WORKER_QC_ITEM_MAX_CONCURRENT", "2"))

# ── SSE gộp mức lô (quy mô lớn) ──────────────────────────────────────────────
# Lô có file_count vượt ngưỡng này → publish per-doc (processing/done, KHÔNG
# phải error) bị bỏ, chỉ event "batch" gộp (throttle theo SSE_MIN_INTERVAL) là
# nguồn tiến độ. Lô nhỏ giữ nguyên hành vi cũ (mọi event bắn ngay).
SSE_AGG_FILE_THRESHOLD = int(os.getenv("AIHUB_SSE_AGG_THRESHOLD", "500"))
SSE_MIN_INTERVAL = float(os.getenv("AIHUB_SSE_MIN_INTERVAL_SECONDS", "2"))
