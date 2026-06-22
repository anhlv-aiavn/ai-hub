"""Hàng đợi RQ — API enqueue, worker tiêu thụ."""

from redis import Redis
from rq import Queue

from app import config

_redis: Redis | None = None
_queue: Queue | None = None


def get_queue() -> Queue:
    global _redis, _queue
    if _queue is None:
        _redis = Redis.from_url(config.REDIS_URL)
        _queue = Queue(config.RQ_QUEUE, connection=_redis, default_timeout=3600)
    return _queue


def enqueue_gcn(gcn_id: str) -> None:
    get_queue().enqueue("app.worker.run_job.process_gcn", gcn_id)
