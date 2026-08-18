"""Worker AI-HUB — streaming pool bất đồng bộ (giống extract_path gốc).

Thay vì RQ tuần tự: claim các gcn doc status=queued từ Mongo và xử lý NHIỀU file
in-flight cùng lúc → nhiều request VLM đồng thời cho vLLM dynamic-batch (trần bởi
_VLM_SEM trong run_job). Job treo (processing quá hạn) được claim lại.

Dead-letter (§Quy mô cực lớn 6): doc processing treo quá PROC_TTL VÀ đã vượt
MAX_ATTEMPTS lần claim (worker chết cứng khi xử lý — không phải exception bắt
được, nếu không nó đã thoát "processing" từ lâu) → "dead", ngừng reclaim, để
không bào mòn cả pool quanh 1 doc độc.

Fairness (§Quy mô cực lớn 7): round-robin theo batch_id khi còn nhiều lô cùng
chờ, tránh 1 lô khổng lồ làm đói vô hạn các lô nộp sau. Cache refresh theo chu
kỳ (không phải mỗi lần claim) để không thêm 1 query nặng vào hot path."""

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument

from app import config
from app.batch_counters import bump
from app.worker.export_job import claim_export_job, process_export_job
from app.worker.import_job import claim_import_job, process_import_job
from app.worker.run_job import process_doc
from src.extentions.mongo_helper import AsyncMongo
from src.extentions.multimodal.vlm_client import endpoint_count, total_vlm_concurrency

log = logging.getLogger(__name__)

POLL_INTERVAL = float(os.getenv("WORKER_POLL_INTERVAL", "2"))
PROC_TTL = int(os.getenv("WORKER_PROC_TTL", "1800"))  # claim lại job processing treo
IMPORT_MAX_CONCURRENT = int(os.getenv("WORKER_IMPORT_MAX_CONCURRENT", "2"))
EXPORT_MAX_CONCURRENT = int(os.getenv("WORKER_EXPORT_MAX_CONCURRENT", "1"))
MAX_ATTEMPTS = int(os.getenv("WORKER_MAX_ATTEMPTS", "3"))
RR_REFRESH_INTERVAL = float(os.getenv("WORKER_FAIRNESS_REFRESH_SECONDS", "3"))
# Dead-letter sweep là tác vụ nền hiếm — KHÔNG cần chạy mỗi vòng poll (vòng lặp
# quay ≤POLL_INTERVAL, có khi sub-giây khi task xong liên tục → mỗi worker quét
# Mongo nhiều lần/giây, nhân theo số worker). Chạy mỗi SWEEP_INTERVAL là đủ:
# poison chỉ được phát hiện sau khi treo > PROC_TTL(30′) nên trễ thêm ~30s vô hại.
SWEEP_INTERVAL = float(os.getenv("WORKER_SWEEP_INTERVAL", "30"))
_last_sweep = 0.0

# Round-robin fairness: cache batch_id có doc queued, refresh theo chu kỳ (không
# phải mỗi lần claim — tránh thêm 1 `distinct` vào hot path).
_rr_batches: list[str] = []
_rr_idx = 0
_rr_refreshed_at = 0.0
# Lô đang TẠM DỪNG. Refresh cùng nhịp round-robin, nên bấm tạm dừng thì trong
# vòng RR_REFRESH_INTERVAL giây worker ngừng nhận việc mới của lô đó.
#
# Vì sao đánh dấu ở LÔ chứ không lật trạng thái từng hồ sơ: lô có thể vài triệu
# doc, lật `queued → paused` rồi lật ngược là hai lần ghi toàn bộ collection, và
# doc đang `processing` thì không lật được nên trạng thái sẽ nửa vời. Chặn ở chỗ
# nhận việc thì tạm dừng là O(1) và hàng đợi giữ nguyên y hệt lúc dừng.
#
# Doc ĐANG chạy dở KHÔNG bị huỷ — không cắt ngang được một call VLM đang bay.
# "Tạm dừng" nghĩa là không nhận thêm việc mới, không phải dừng tức khắc.
_tam_dung: frozenset = frozenset()


def _dieu_kien_claim(batch_id: str | None, tam_dung: frozenset, stale) -> dict:
    """Bộ lọc Mongo cho `_claim`. Tách riêng cho THUẦN để smoke được: đây là chỗ
    một thiếu sót im lặng sẽ khiến lô đã tạm dừng vẫn tiếp tục chạy."""
    queued: dict = {"status": "queued"}
    treo: dict = {"status": "processing", "started_at": {"$lt": stale}}
    if batch_id:
        # batch_id đến từ round-robin, vốn ĐÃ loại lô tạm dừng.
        queued["batch_id"] = batch_id
    elif tam_dung:
        queued["batch_id"] = {"$nin": sorted(tam_dung)}
    if tam_dung:
        # Nhánh reclaim doc treo: KHÔNG giới hạn batch_id để reclaim luôn chạy
        # được, nhưng lô đã tạm dừng thì vẫn phải chừa ra — nếu không, bấm tạm
        # dừng xong 30 phút sau doc treo của chính lô đó lại được nhặt lên chạy.
        treo["batch_id"] = {"$nin": sorted(tam_dung)}
    return {"$or": [queued, treo]}


async def _next_batch_id(mongo: AsyncMongo) -> str | None:
    global _rr_batches, _rr_idx, _rr_refreshed_at, _tam_dung
    now = time.monotonic()
    if now - _rr_refreshed_at >= RR_REFRESH_INTERVAL:
        rows = await mongo.db[config.COLL_GCN].distinct("batch_id", {"status": "queued"})
        _tam_dung = frozenset(
            await mongo.db[config.COLL_BATCH].distinct("_id", {"paused": True}))
        _rr_batches = [b for b in rows if b and b not in _tam_dung]
        _rr_idx = 0
        _rr_refreshed_at = now
    if not _rr_batches:
        return None
    picked = _rr_batches[_rr_idx % len(_rr_batches)]
    _rr_idx += 1
    return picked


async def _claim(mongo: AsyncMongo, batch_id: str | None = None) -> dict | None:
    """Atomic claim 1 doc: queued (giới hạn `batch_id` nếu truyền — round-robin
    fairness), hoặc processing đã treo quá PROC_TTL (LUÔN không giới hạn batch —
    reclaim hiếm xảy ra, không cần fairness — nhưng VẪN chừa lô tạm dừng, xem
    `_dieu_kien_claim`). `$inc attempts` mỗi lần claim (kể cả reclaim) — nền tảng
    cho dead-letter (poison doc bị worker sweep riêng, xem `_sweep_dead`, nên
    nhánh reclaim ở đây luôn còn "sống")."""
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=PROC_TTL)
    doc = await mongo.db[config.COLL_GCN].find_one_and_update(
        _dieu_kien_claim(batch_id, _tam_dung, stale),
        {"$set": {"status": "processing", "started_at": now}, "$inc": {"attempts": 1}},
        return_document=ReturnDocument.BEFORE,
    )
    if not doc:
        return None
    if doc.get("status") == "queued":  # reclaim từ processing treo đã đếm processing từ trước
        await bump(mongo.db[config.COLL_BATCH], doc.get("batch_id"), queued=-1, processing=1)
    doc["status"] = "processing"
    doc["started_at"] = now
    doc["attempts"] = (doc.get("attempts") or 0) + 1
    return doc


async def _sweep_dead(mongo: AsyncMongo) -> None:
    """Chuyển doc processing-treo-quá-hạn-và-vượt-MAX_ATTEMPTS sang "dead". Tự
    THROTTLE mỗi SWEEP_INTERVAL (không chạy mỗi vòng poll — xem SWEEP_INTERVAL)."""
    global _last_sweep
    now_m = time.monotonic()
    if now_m - _last_sweep < SWEEP_INTERVAL:
        return
    _last_sweep = now_m
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=PROC_TTL)
    cursor = mongo.db[config.COLL_GCN].find(
        {"status": "processing", "started_at": {"$lt": stale}, "attempts": {"$gte": MAX_ATTEMPTS}},
        {"batch_id": 1},
    )
    async for doc in cursor:
        res = await mongo.db[config.COLL_GCN].update_one(
            {"_id": doc["_id"], "status": "processing"},
            {"$set": {"status": "dead", "error_kind": "poison",
                      "error": f"Vượt quá {MAX_ATTEMPTS} lần xử lý — nghi ngờ làm worker treo (poison doc)"}},
        )
        if res.modified_count:
            await bump(mongo.db[config.COLL_BATCH], doc.get("batch_id"), processing=-1, dead=1)


async def run() -> None:
    mongo = AsyncMongo()
    while not await mongo.ping():
        log.warning("Chưa kết nối được Mongo, thử lại…")
        await asyncio.sleep(2)
    log.info("Worker khởi động · %d endpoint vLLM · trần/máy=%d · trần tổng=%d · MAX_IN_FLIGHT=%d",
             endpoint_count(), config.MAX_VLM_CONCURRENT, total_vlm_concurrency(), config.MAX_IN_FLIGHT)

    in_flight: set[asyncio.Task] = set()
    import_in_flight: set[asyncio.Task] = set()
    export_in_flight: set[asyncio.Task] = set()
    while True:
        await _sweep_dead(mongo)

        while len(in_flight) < config.MAX_IN_FLIGHT:
            batch_id = await _next_batch_id(mongo)
            doc = await _claim(mongo, batch_id) if batch_id else None
            if not doc:  # lô ưu tiên vừa hết (hoặc không còn lô nào) → claim không giới hạn
                doc = await _claim(mongo)
            if not doc:
                break
            in_flight.add(asyncio.create_task(process_doc(mongo, doc)))

        # Claim import_jobs (thư mục lớn) — nhẹ, tách riêng khỏi trần
        # MAX_IN_FLIGHT (đó là để bound RAM ảnh render GCN, không áp dụng ở đây).
        while len(import_in_flight) < IMPORT_MAX_CONCURRENT:
            job = await claim_import_job(mongo, PROC_TTL, _tam_dung)
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


def _smoke() -> None:
    """PURE cho bộ lọc claim — chỗ mà một thiếu sót im lặng sẽ khiến lô đã bấm
    tạm dừng vẫn chạy tiếp, mà nhìn log thì không thấy gì bất thường."""
    RONG = frozenset()
    DUNG = frozenset({"b2", "b1"})

    f = _dieu_kien_claim(None, RONG, "S")
    assert f == {"$or": [{"status": "queued"},
                         {"status": "processing", "started_at": {"$lt": "S"}}]}, \
        f"KILL [1] không có lô tạm dừng → bộ lọc y hệt trước khi có tính năng: {f}"

    f = _dieu_kien_claim("bx", RONG, "S")
    assert f["$or"][0] == {"status": "queued", "batch_id": "bx"}, \
        f"KILL [2] round-robin vẫn giới hạn đúng lô: {f}"

    f = _dieu_kien_claim(None, DUNG, "S")
    assert f["$or"][0]["batch_id"] == {"$nin": ["b1", "b2"]}, \
        f"KILL [3] claim không giới hạn phải chừa lô tạm dừng: {f}"
    assert f["$or"][1]["batch_id"] == {"$nin": ["b1", "b2"]}, \
        ("KILL [4] nhánh RECLAIM doc treo cũng phải chừa — quên chỗ này thì bấm tạm "
         f"dừng xong doc treo của chính lô đó vẫn được nhặt lên chạy: {f}")
    assert sorted(f["$or"][1]) == ["$or"] or "started_at" in f["$or"][1], \
        "KILL [5] nhánh reclaim vẫn phải giữ điều kiện treo, không claim bừa doc đang chạy"

    f = _dieu_kien_claim("bx", DUNG, "S")
    assert f["$or"][0]["batch_id"] == "bx", \
        f"KILL [6] có batch_id thì giữ nguyên, không chồng thêm $nin: {f}"

    # Thứ tự ổn định: bộ lọc đi vào Mongo, khác nhau mỗi lần gọi là mất cả khả
    # năng dùng index lẫn khả năng so log giữa hai worker.
    assert _dieu_kien_claim(None, frozenset({"b2", "b1"}), "S") == \
        _dieu_kien_claim(None, frozenset({"b1", "b2"}), "S"), "KILL [7] bộ lọc phải tất định"
    print("worker.main PURE: 7 KILL ✓")


if __name__ == "__main__":
    if "--tu-kiem" in sys.argv:
        _smoke()
    else:
        main()
