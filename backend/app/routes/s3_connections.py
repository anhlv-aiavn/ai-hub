"""Cấu hình nguồn/đích S3 (MinIO) — admin-only. API KHÔNG BAO GIỜ trả secret đã
lưu; import/browse (operator) dùng connection theo `id`, server giữ secret."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app import storage
from app.audit import AuditAction, log_action
from app.db import gcns, s3_connections
from app.deps import require_admin
from app.s3_util import async_test_connection

router = APIRouter(prefix="/v1/s3-connections", tags=["s3-connections"],
                   dependencies=[Depends(require_admin)])


class ConnIn(BaseModel):
    role: str                      # source | destination
    name: str
    endpoint_url: str
    access_key_id: str
    secret_access_key: str = ""
    bucket: str
    verify_tls: bool = False


class ConnPatch(BaseModel):
    name: str | None = None
    endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None   # rỗng/None = giữ nguyên
    bucket: str | None = None
    verify_tls: bool | None = None


class TestDraft(BaseModel):
    id: str | None = None
    use_stored_secret: bool = False
    role: str = "source"
    endpoint_url: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""
    bucket: str = ""
    verify_tls: bool = False


def _public(c: dict) -> dict:
    return {
        "id": c["_id"], "role": c.get("role"), "name": c.get("name"),
        "endpoint_url": c.get("endpoint_url"), "access_key_id": c.get("access_key_id"),
        "secret_set": bool(c.get("secret_access_key")), "bucket": c.get("bucket"),
        "verify_tls": bool(c.get("verify_tls")),
        "created_at": c.get("created_at"), "updated_at": c.get("updated_at"),
        "last_checked_at": c.get("last_checked_at"),
        "last_check_status": c.get("last_check_status"),
        "last_check_message": c.get("last_check_message"),
    }


@router.get("")
async def list_conns(role: str | None = Query(default=None)):
    flt = {"role": role} if role else {}
    rows = await s3_connections().find(flt).sort("name", 1).to_list(length=500)
    return {"connections": [_public(c) for c in rows]}


@router.post("")
async def create_conn(body: ConnIn, admin: dict = Depends(require_admin)):
    if body.role not in ("source", "destination"):
        raise HTTPException(status_code=400, detail="role phải là source hoặc destination")
    if not body.secret_access_key:
        raise HTTPException(status_code=400, detail="Cần secret_access_key khi tạo mới")
    now = datetime.now(timezone.utc)
    doc = {
        "_id": str(uuid.uuid4()), "role": body.role, "name": body.name,
        "endpoint_url": body.endpoint_url, "access_key_id": body.access_key_id,
        "secret_access_key": body.secret_access_key, "bucket": body.bucket,
        "verify_tls": body.verify_tls, "created_at": now, "updated_at": now,
        "last_checked_at": None, "last_check_status": None, "last_check_message": None,
    }
    await s3_connections().insert_one(doc)
    storage.invalidate_s3_cache()
    await log_action(admin["username"], AuditAction.S3_CONNECTION_CREATE, doc["_id"],
                     {"after": {k: v for k, v in doc.items() if k != "_id"}})
    return {"ok": True, "connection": _public(doc)}


@router.patch("/{conn_id}")
async def update_conn(conn_id: str, body: ConnPatch, admin: dict = Depends(require_admin)):
    before = await s3_connections().find_one({"_id": conn_id})
    if not before:
        raise HTTPException(status_code=404, detail="Không tìm thấy connection")
    upd: dict = {}
    for field in ("name", "endpoint_url", "access_key_id", "bucket", "verify_tls"):
        v = getattr(body, field)
        if v is not None:
            upd[field] = v
    if body.secret_access_key:  # rỗng = giữ nguyên
        upd["secret_access_key"] = body.secret_access_key
    if upd:
        upd["updated_at"] = datetime.now(timezone.utc)
        await s3_connections().update_one({"_id": conn_id}, {"$set": upd})
        storage.invalidate_s3_cache()
    after = await s3_connections().find_one({"_id": conn_id})
    await log_action(admin["username"], AuditAction.S3_CONNECTION_UPDATE, conn_id,
                     {"before": before, "after": after})
    return {"ok": True, "connection": _public(after)}


@router.delete("/{conn_id}")
async def delete_conn(conn_id: str, force: bool = False, admin: dict = Depends(require_admin)):
    doc = await s3_connections().find_one({"_id": conn_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy connection")
    n_ref = await gcns().count_documents({"source_connection_id": conn_id})
    if n_ref and not force:
        raise HTTPException(status_code=409, detail={
            "message": "Connection còn được tham chiếu bởi GCN đã import",
            "referenced_gcns": n_ref,
        })
    await s3_connections().delete_one({"_id": conn_id})
    storage.invalidate_s3_cache()
    await log_action(admin["username"], AuditAction.S3_CONNECTION_DELETE, conn_id,
                     {"deleted": doc})
    return {"ok": True, "referenced_gcns": n_ref}


@router.post("/test")
async def test_draft(body: TestDraft):
    """Test cấu hình CHƯA lưu (draft). KHÔNG ghi audit — chưa có target/mutation."""
    secret = body.secret_access_key
    endpoint_url, access_key_id, bucket, verify_tls = (
        body.endpoint_url, body.access_key_id, body.bucket, body.verify_tls)
    if body.use_stored_secret and body.id:
        stored = await s3_connections().find_one({"_id": body.id})
        if not stored:
            raise HTTPException(status_code=404, detail="Không tìm thấy connection để lấy secret")
        secret = stored["secret_access_key"]
        endpoint_url = endpoint_url or stored["endpoint_url"]
        access_key_id = access_key_id or stored["access_key_id"]
        # KHÔNG fallback bucket về stored["bucket"]: test draft giờ luôn test theo
        # kiểu "liệt kê bucket" khi bucket rỗng (đổi bucket đã lưu cũng cần list lại).
    if not secret:
        raise HTTPException(status_code=400, detail="Cần secret_access_key để test")
    return await async_test_connection(endpoint_url, access_key_id, secret, bucket,
                                       verify_tls, body.role)


@router.post("/{conn_id}/test")
async def test_saved(conn_id: str, admin: dict = Depends(require_admin)):
    """Test connection ĐÃ lưu (dùng secret đã lưu). Ghi audit + cập nhật last_check_*."""
    doc = await s3_connections().find_one({"_id": conn_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Không tìm thấy connection")
    result = await async_test_connection(
        doc["endpoint_url"], doc["access_key_id"], doc["secret_access_key"],
        doc["bucket"], bool(doc.get("verify_tls")), doc.get("role", "source"),
    )
    await s3_connections().update_one({"_id": conn_id}, {"$set": {
        "last_checked_at": datetime.now(timezone.utc),
        "last_check_status": result["result"],
        "last_check_message": result["message"],
    }})
    await log_action(admin["username"], AuditAction.S3_CONNECTION_TEST, conn_id, result)
    return result
