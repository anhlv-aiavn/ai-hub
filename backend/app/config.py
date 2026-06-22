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

# ── Auth tùy chọn: nếu set → bắt buộc X-API-Key (Sobagi cấp key) ─────────────
API_KEY = os.getenv("AIHUB_API_KEY", "").strip()

# ── Đối soát: DPI/scale render ảnh trang ────────────────────────────────────
PAGE_RENDER_MAX_W = int(os.getenv("PAGE_RENDER_MAX_W", "2200"))
