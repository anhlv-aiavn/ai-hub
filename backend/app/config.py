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

# ── MinIO (kho riêng AI-HUB) — biến ENDPOINT_URL_MINIO/… do minio_helper đọc ─
AIHUB_BUCKET = os.getenv("AIHUB_BUCKET", "ai-hub")

# ── Redis (bus SSE) ─────────────────────────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
EVENT_CHANNEL = os.getenv("EVENT_CHANNEL", "aihub:events")

# ── Worker concurrency (in-flight batching cho vLLM) ────────────────────────
# Số call VLM đồng thời tối đa (detect+extract) — bơm để vLLM dynamic-batch.
MAX_VLM_CONCURRENT = int(os.getenv("MAX_VLM_CONCURRENT", "8"))
# Số file in-flight tối đa (bound RAM ảnh render).
MAX_IN_FLIGHT = int(os.getenv("MAX_IN_FLIGHT", str(MAX_VLM_CONCURRENT * 3)))

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

# ── Đối soát: DPI/scale render ảnh trang ────────────────────────────────────
PAGE_RENDER_MAX_W = int(os.getenv("PAGE_RENDER_MAX_W", "2200"))
