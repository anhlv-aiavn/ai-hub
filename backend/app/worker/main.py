"""Điểm vào RQ worker. Dùng SimpleWorker (KHÔNG fork) vì engine GCN dùng
ProcessPoolExecutor + asyncio — fork giữa các job sẽ hỏng executor."""

import logging

from redis import Redis
from rq import SimpleWorker

from app import config
# Import sớm để mọi ImportError của pipeline hiện thẳng lúc khởi động worker
# (tránh RQ nuốt lỗi thành "module has no attribute run_job" lúc chạy job).
from app.worker import run_job  # noqa: F401


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    conn = Redis.from_url(config.REDIS_URL)
    worker = SimpleWorker([config.RQ_QUEUE], connection=conn)
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
