"""Truy cập MongoDB cho API (motor, long-lived). Worker dùng client riêng/job."""

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app import config

_client: AsyncIOMotorClient | None = None


def get_db() -> AsyncIOMotorDatabase:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(config.MONGO_URI)
    return _client[config.MONGO_DB]


def batches():
    return get_db()[config.COLL_BATCH]


def gcns():
    return get_db()[config.COLL_GCN]


def users():
    return get_db()[config.COLL_USER]


def site_config():
    return get_db()[config.COLL_SITE_CONFIG]


def s3_connections():
    return get_db()[config.COLL_S3_CONN]


def audit_log():
    return get_db()[config.COLL_AUDIT]


def import_jobs():
    return get_db()[config.COLL_IMPORT_JOB]


def export_jobs():
    return get_db()[config.COLL_EXPORT_JOB]


def browse_progress_cache():
    return get_db()[config.COLL_BROWSE_PROGRESS]


def qc_sync_configs():
    return get_db()[config.COLL_QC_SYNC_CONFIG]


def qc_sync_jobs():
    return get_db()[config.COLL_QC_SYNC_JOB]


def qc_items():
    return get_db()[config.COLL_QC_ITEM]


def qc_stats_daily():
    return get_db()[config.COLL_QC_STATS_DAILY]


async def ensure_indexes() -> None:
    await gcns().create_index("batch_id")
    await gcns().create_index("group_key")
    await gcns().create_index("status")
    await gcns().create_index("branch")
    await gcns().create_index("extracted_so_phat_hanhs")
    # Hàng đợi "cần xác minh chủ cuối" — partial index: chỉ ~1% hồ sơ có cảnh báo
    # nên index gọn, lọc nhanh ở quy mô trăm nghìn (xem routes/gcn.py `canh_bao`).
    await gcns().create_index(
        "chu_cuoi.canh_bao", background=True, name="canh_bao_chu_cuoi",
        partialFilterExpression={"chu_cuoi.canh_bao": {"$exists": True}},
    )
    await batches().create_index("created_at")
    await batches().create_index("branch")
    await users().create_index("username", unique=True)
    await users().create_index("assigned_batch_ids")
    await audit_log().create_index([("at", -1)])
    await audit_log().create_index("actor")
    await audit_log().create_index("action")
    await audit_log().create_index("target")
    await gcns().create_index(
        [("source_connection_id", 1), ("s3_key", 1), ("batch_id", 1)],
        unique=True, background=True,
        partialFilterExpression={"source_connection_id": {"$exists": True}},
        name="uniq_source_key_batch",
    )
    await import_jobs().create_index("status")
    await import_jobs().create_index("started_at")
    await export_jobs().create_index("status")
    await gcns().create_index([("branch", 1), ("created_at", -1)])
    # Bảng "Xuất dữ liệu" + "Tải CSV" sort mới-nhất-trước rồi limit: thiếu index
    # này thì sort toàn kho (~600k) chạy trong RAM và Mongo chặn ở 32MB
    # ("Sort exceeded memory limit"). Với index, sort+limit chỉ đi ngược index.
    await gcns().create_index([("created_at", -1)], background=True, name="created_at_desc")
    # TTL: Mongo tự xóa cache tiến độ MinIO đã hết hạn — không cần dọn tay/kiểm
    # tra tuổi bằng Python (xem app.config.BROWSE_PROGRESS_CACHE_TTL).
    await browse_progress_cache().create_index(
        "computed_at", expireAfterSeconds=config.BROWSE_PROGRESS_CACHE_TTL,
    )
    # ── QC Sync (pipeline mới — xem app/worker/qc_pipeline.py) ─────────────
    await qc_sync_jobs().create_index("status")
    await qc_sync_jobs().create_index("started_at")
    await qc_sync_jobs().create_index("config_id")
    # "Không chạy lại file đã QC": insert optimistic (insert_many ordered=False),
    # unique index từ chối êm key trùng — đúng khuôn uniq_source_key_batch (gcn).
    await qc_items().create_index(
        [("source_connection_id", 1), ("s3_key", 1)],
        unique=True, background=True, name="uniq_qc_source_key",
    )
    await qc_items().create_index("status")
    await qc_items().create_index("config_id")
    await qc_items().create_index([("created_at", -1)], background=True, name="qc_items_created_at_desc")
    # Rollup ngày để trả "mỗi ngày/tuần/tổng" mà KHÔNG count_documents trên
    # qc_items ở quy mô lớn — xem app/qc_client.py + worker/qc_pipeline.py.
    await qc_stats_daily().create_index([("config_id", 1), ("date", 1)], unique=True)
