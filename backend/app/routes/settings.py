"""Cấu hình tổ chức (1 deployment = 1 cấu hình duy nhất): tên, danh sách chi
nhánh, branding. Sửa qua Admin UI, không cần build lại image."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.audit import AuditAction, log_action
from app.branches import invalidate_site_cache
from app.db import batches, gcns, site_config, users
from app.deps import require_admin

router = APIRouter(prefix="/v1/settings", tags=["settings"])


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
