"""Bus sự kiện realtime qua Redis pub/sub — worker (process riêng) publish,
API SSE subscribe. Cho phép trạng thái lô chạy live trên UI."""

import json

import redis
import redis.asyncio as aioredis

from app import config


def publish_sync(event: dict) -> None:
    """Gọi từ worker (đồng bộ). Bỏ qua lỗi để không làm hỏng job."""
    try:
        r = redis.Redis.from_url(config.REDIS_URL)
        r.publish(config.EVENT_CHANNEL, json.dumps(event, default=str))
        r.close()
    except Exception:
        pass


async def subscribe():
    """Async generator → yield từng event dict cho SSE."""
    r = aioredis.Redis.from_url(config.REDIS_URL)
    pubsub = r.pubsub()
    await pubsub.subscribe(config.EVENT_CHANNEL)
    try:
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            data = msg.get("data")
            if isinstance(data, (bytes, bytearray)):
                data = data.decode("utf-8", "ignore")
            try:
                yield json.loads(data)
            except (ValueError, TypeError):
                continue
    finally:
        try:
            await pubsub.unsubscribe(config.EVENT_CHANNEL)
            await pubsub.aclose()
            await r.aclose()
        except Exception:
            pass
