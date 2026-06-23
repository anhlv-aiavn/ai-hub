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


async def ensure_indexes() -> None:
    await gcns().create_index("batch_id")
    await gcns().create_index("group_key")
    await gcns().create_index("status")
    await gcns().create_index("branch")
    await gcns().create_index("extracted_so_phat_hanhs")
    await batches().create_index("created_at")
    await batches().create_index("branch")
    await users().create_index("username", unique=True)
