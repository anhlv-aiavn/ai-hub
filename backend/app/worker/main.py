"""Điểm vào RQ worker. Dùng SimpleWorker (KHÔNG fork) vì engine GCN dùng
ProcessPoolExecutor + asyncio — fork giữa các job sẽ hỏng executor."""

import logging

from redis import Redis
from rq import SimpleWorker

from app import config


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
