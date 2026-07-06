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
from app.worker.export_job import claim_export_job, process_export_job
from app.worker.import_job import claim_import_job, process_import_job
from app.worker.run_job import process_doc
from src.extentions.mongo_helper import AsyncMongo

log = logging.getLogger(__name__)

POLL_INTERVAL = float(os.getenv("WORKER_POLL_INTERVAL", "2"))
PROC_TTL = int(os.getenv("WORKER_PROC_TTL", "1800"))  # claim lại job processing treo
IMPORT_MAX_CONCURRENT = int(os.getenv("WORKER_IMPORT_MAX_CONCURRENT", "2"))
EXPORT_MAX_CONCURRENT = int(os.getenv("WORKER_EXPORT_MAX_CONCURRENT", "1"))


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
    import_in_flight: set[asyncio.Task] = set()
    export_in_flight: set[asyncio.Task] = set()
    while True:
        while len(in_flight) < config.MAX_IN_FLIGHT:
            doc = await _claim(mongo)
            if not doc:
                break
            in_flight.add(asyncio.create_task(process_doc(mongo, doc)))

        # Claim import_jobs (thư mục lớn) — nhẹ, tách riêng khỏi trần
        # MAX_IN_FLIGHT (đó là để bound RAM ảnh render GCN, không áp dụng ở đây).
        while len(import_in_flight) < IMPORT_MAX_CONCURRENT:
            job = await claim_import_job(mongo, PROC_TTL)
            if not job:
                break
            import_in_flight.add(asyncio.create_task(process_import_job(mongo, job)))

        while len(export_in_flight) < EXPORT_MAX_CONCURRENT:
            job = await claim_export_job(mongo, PROC_TTL)
            if not job:
                break
            export_in_flight.add(asyncio.create_task(process_export_job(mongo, job)))

        pending = in_flight | import_in_flight | export_in_flight
        if not pending:
            await asyncio.sleep(POLL_INTERVAL)
            continue

        done, _ = await asyncio.wait(
            pending, timeout=POLL_INTERVAL, return_when=asyncio.FIRST_COMPLETED,
        )
        in_flight -= done
        import_in_flight -= done
        export_in_flight -= done
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
