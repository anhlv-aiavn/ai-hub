"""Cấu hình tổ chức (1 deployment = 1 cấu hình duy nhất): tên, danh sách chi
nhánh, branding. Sửa qua Admin UI, không cần build lại image."""

import time

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel

from app import storage
from app.audit import AuditAction, log_action
from app.branches import invalidate_site_cache
from app.branding_image import validate_and_normalize_logo
from app.db import batches, gcns, s3_connections, site_config, users
from app.deps import require_admin, require_viewer
from app.storage import DestinationNotConfigured

router = APIRouter(prefix="/v1/settings", tags=["settings"])

LOGO_KEY = "branding/logo.png"


class Branding(BaseModel):
    org_name: str = ""
    logo_url: str = ""
    copyright_text: str = ""


class SiteIn(BaseModel):
    name: str | None = None
    branches: list[str] | None = None
    branding: Branding | None = None


def _public(doc: dict) -> dict:
    return {
        "name": doc.get("name", ""),
        "branches": doc.get("branches", []),
        "branding": doc.get("branding", {}),
    }


@router.get("/site")
async def get_site(admin: dict = Depends(require_admin)):
    doc = await site_config().find_one({"_id": "site"}) or {}
    return _public(doc)


@router.patch("/site")
async def update_site(body: SiteIn, confirm: bool = False, admin: dict = Depends(require_admin)):
    before = await site_config().find_one({"_id": "site"}) or {}
    upd: dict = {}
    if body.name is not None:
        upd["name"] = body.name
    if body.branches is not None:
        if not body.branches:
            raise HTTPException(status_code=400, detail="Danh sách chi nhánh không được rỗng")
        removed = set(before.get("branches") or []) - set(body.branches)
        if removed and not confirm:
            n_users = await users().count_documents({"branch": {"$in": list(removed)}})
            n_batches = await batches().count_documents({"branch": {"$in": list(removed)}})
            n_gcns = await gcns().count_documents({"branch": {"$in": list(removed)}})
            if n_users or n_batches or n_gcns:
                raise HTTPException(status_code=409, detail={
                    "message": "Chi nhánh sắp bị bớt vẫn còn được tham chiếu",
                    "removed_branches": sorted(removed),
                    "users": n_users, "batches": n_batches, "gcns": n_gcns,
                })
        upd["branches"] = body.branches
    if body.branding is not None:
        upd["branding"] = body.branding.model_dump()

    if upd:
        await site_config().update_one({"_id": "site"}, {"$set": upd}, upsert=True)
    after = await site_config().find_one({"_id": "site"}) or {}
    invalidate_site_cache()
    await log_action(admin["username"], AuditAction.SITE_CONFIG_UPDATE, "site",
                     {"before": _public(before), "after": _public(after)})
    return _public(after)


@router.get("/branding")
async def get_branding():
    """Public — Login cần trước khi đăng nhập. Không field nhạy cảm."""
    doc = await site_config().find_one({"_id": "site"}) or {}
    return doc.get("branding") or {}


@router.get("/status")
async def get_status(user: dict = Depends(require_viewer)):
    """Trạng thái nhẹ cho UI (mọi role đã đăng nhập) — KHÔNG lộ endpoint/bucket/
    secret, chỉ để FE biết có nên chặn upload/export/logo hay không (§Backend 5)."""
    configured = await s3_connections().count_documents({"role": "destination"}) > 0
    return {"destination_configured": configured}


@router.post("/logo")
async def upload_logo(file: UploadFile = File(...), admin: dict = Depends(require_admin)):
    data = await file.read()
    try:
        normalized = validate_and_normalize_logo(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        await storage.put_object(LOGO_KEY, normalized)
    except DestinationNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    before = await site_config().find_one({"_id": "site"}) or {}
    logo_url = f"/v1/settings/logo?v={int(time.time())}"  # query bust cache trình duyệt sau khi đổi logo
    branding = dict(before.get("branding") or {})
    branding["logo_url"] = logo_url
    await site_config().update_one({"_id": "site"}, {"$set": {"branding": branding}}, upsert=True)
    after = await site_config().find_one({"_id": "site"}) or {}
    invalidate_site_cache()
    await log_action(admin["username"], AuditAction.SITE_CONFIG_UPDATE, "site",
                     {"before": _public(before), "after": _public(after)})
    return _public(after)


@router.get("/logo")
async def get_logo():
    """Public (Login cần logo trước khi đăng nhập, cùng lý do với /branding)."""
    try:
        data = await storage.get_object(LOGO_KEY)
    except DestinationNotConfigured:
        raise HTTPException(status_code=404, detail="Chưa có logo tải lên") from None
    except Exception:  # noqa: BLE001 — chưa từng upload hoặc lỗi đọc đích → 404 (FE có fallback ảnh mặc định)
        raise HTTPException(status_code=404, detail="Chưa có logo tải lên") from None
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})
