"""Counter duy trì trên `batch.counts` — thay `count_documents`/aggregate lặp lại
mỗi lần cần biết tiến độ (đắt ở quy mô chục triệu doc). Mọi điểm chuyển trạng thái
gcn gọi `bump()` cạnh update Mongo hiện có; đọc tiến độ chỉ cần đọc field này.

Trạng thái theo dõi: queued, processing, done, error, no_gcn, skip, dead.
("no_gcn" = hồ sơ không chứa giấy chứng nhận — kết quả hợp lệ, tách khỏi "error".)"""

from pymongo import ReturnDocument

_STATUSES = ("queued", "processing", "done", "error", "no_gcn", "skip", "dead")


def zero_counts() -> dict:
    return {s: 0 for s in _STATUSES}


async def init_counts(coll_batch, batch_id: str, queued: int = 0) -> None:
    """Gọi lúc TẠO lô (không phải nối thêm) — set counts ban đầu."""
    counts = zero_counts()
    counts["queued"] = queued
    await coll_batch.update_one({"_id": batch_id}, {"$set": {"counts": counts}})


async def bump(coll_batch, batch_id: str | None, **deltas: int) -> dict | None:
    """`$inc` nguyên tử các field `counts.<status>` theo `deltas` (vd bump(coll,
    id, queued=-1, processing=1)). Bỏ qua êm nếu batch_id rỗng/không tồn tại —
    không phải lỗi nghiệp vụ, chỉ là thiếu ngữ cảnh lô (vd cut không thuộc batch)."""
    if not batch_id or not deltas:
        return None
    inc = {f"counts.{k}": v for k, v in deltas.items() if v}
    if not inc:
        return None
    doc = await coll_batch.find_one_and_update(
        {"_id": batch_id}, {"$inc": inc}, return_document=ReturnDocument.AFTER,
    )
    return (doc or {}).get("counts")
