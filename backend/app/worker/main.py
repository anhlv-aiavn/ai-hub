"""Worker AI-HUB — streaming pool bất đồng bộ (giống extract_path gốc).

Thay vì RQ tuần tự: claim các gcn doc status=queued từ Mongo và xử lý NHIỀU file
in-flight cùng lúc → nhiều request VLM đồng thời cho vLLM dynamic-batch (trần bởi
_VLM_SEM trong run_job). Job treo (processing quá hạn) được claim lại."""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument

from app import config
from app.worker.run_job import process_doc
from src.extentions.mongo_helper import AsyncMongo

log = logging.getLogger(__name__)

POLL_INTERVAL = float(os.getenv("WORKER_POLL_INTERVAL", "2"))
PROC_TTL = int(os.getenv("WORKER_PROC_TTL", "1800"))  # claim lại job processing treo


async def _claim(mongo: AsyncMongo) -> dict | None:
    """Atomic claim 1 doc: queued, hoặc processing đã treo quá PROC_TTL."""
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=PROC_TTL)
    return await mongo.db[config.COLL_GCN].find_one_and_update(
        {"$or": [
            {"status": "queued"},
            {"status": "processing", "started_at": {"$lt": stale}},
        ]},
        {"$set": {"status": "processing", "started_at": now}},
        return_document=ReturnDocument.AFTER,
    )


async def run() -> None:
    mongo = AsyncMongo()
    while not await mongo.ping():
        log.warning("Chưa kết nối được Mongo, thử lại…")
        await asyncio.sleep(2)
    log.info("Worker khởi động · MAX_VLM_CONCURRENT=%d · MAX_IN_FLIGHT=%d",
             config.MAX_VLM_CONCURRENT, config.MAX_IN_FLIGHT)

    in_flight: set[asyncio.Task] = set()
    while True:
        while len(in_flight) < config.MAX_IN_FLIGHT:
            doc = await _claim(mongo)
            if not doc:
                break
            in_flight.add(asyncio.create_task(process_doc(mongo, doc)))

        if not in_flight:
            await asyncio.sleep(POLL_INTERVAL)
            continue

        done, in_flight = await asyncio.wait(
            in_flight, timeout=POLL_INTERVAL, return_when=asyncio.FIRST_COMPLETED,
        )
        for t in done:
            exc = t.exception()
            if exc:
                log.exception("Task lỗi: %s", exc)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
