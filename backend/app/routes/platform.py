"""R1.9 — /api/platform/*: brokerage setup wizard, tenant status, Ops, backups/drills. platform_admin only; no client data."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.kernel import backup, ops, platform

router = APIRouter(prefix="/api/platform")


def _store(request: Request):
    return request.app.state.mongo_db


class TenantBody(BaseModel):
    slug: str = Field(max_length=31)
    legal_name: str = Field(max_length=200)
    display_name: str | None = Field(default=None, max_length=120)
    abn: str = Field(max_length=20)
    afsl: str = Field(max_length=40)
    state: str = Field(max_length=3)
    timezone: str = Field(default="Australia/Sydney", max_length=60)
    sender_name: str | None = Field(default=None, max_length=80)
    reply_to: str | None = Field(default=None, max_length=200)
    operator_email: str = Field(max_length=200)
    operator_name: str = Field(default="", max_length=120)
    test: bool = False


@router.get("/me")
async def me(s=Depends(platform.require_platform_admin)) -> dict:
    return {"role": "platform_admin", "user_id": s.user_id}


@router.get("/tenants")
async def list_tenants(request: Request, s=Depends(platform.require_platform_admin)) -> dict:
    return {"tenants": await platform.tenants(_store(request)), "states": platform.STATES}


@router.post("/tenants")
async def create_tenant(body: TenantBody, request: Request, s=Depends(platform.require_platform_admin)) -> dict:
    out = await platform.create(_store(request), body=body.model_dump(), by=s.user_id)
    return {**out, "checklist": await platform.checklist(_store(request), out["tenant_id"])}


@router.get("/tenants/{tenant_id}")
async def tenant(tenant_id: str, request: Request, s=Depends(platform.require_platform_admin)) -> dict:
    row = next((t for t in await platform.tenants(_store(request)) if t["tenant_id"] == tenant_id), None)
    if not row:
        raise HTTPException(status_code=404, detail="tenant_not_found")
    return row


class StatusBody(BaseModel):
    status: str
    reason: str = Field(default="", max_length=300)
    force: bool = False


@router.put("/tenants/{tenant_id}/status")
async def status(tenant_id: str, body: StatusBody, request: Request, s=Depends(platform.require_platform_admin)) -> dict:
    return await platform.set_status(_store(request), tenant_id, body.status, body.reason, s.user_id, body.force)


@router.get("/ops")
async def ops_summary(request: Request, s=Depends(platform.require_platform_admin)) -> dict:
    return await ops.summary(_store(request))


@router.post("/ops/force-error")
async def force_error(s=Depends(platform.require_platform_admin)) -> dict:
    raise RuntimeError("Forced test error from the Ops page")


@router.post("/ops/watch")
async def run_watch(request: Request, s=Depends(platform.require_platform_admin)) -> dict:
    return {"fired": await ops.watch(_store(request))}


@router.get("/backups")
async def backups(request: Request, s=Depends(platform.require_platform_admin)) -> dict:
    st = _store(request)
    rows = [b async for b in st.backups.find({}, {"_id": 0, "path": 0}).sort("at", -1).limit(50)]  # tenant-safe: system-only
    drills = [d async for d in st.restore_drills.find({}, {"_id": 0}).sort("at", -1).limit(20)]  # tenant-safe: system-only
    return {"backups": rows, "drills": drills}


@router.post("/backups/run")
async def run_backup(request: Request, s=Depends(platform.require_platform_admin)) -> dict:
    return {"backups": [{k: v for k, v in b.items() if k != "path"} for b in await backup.backup_all(_store(request), by=s.user_id)]}


@router.post("/backups/drill/{tenant_id}")
async def run_drill(tenant_id: str, request: Request, s=Depends(platform.require_platform_admin)) -> dict:
    return await backup.drill(_store(request), tenant_id, by=s.user_id)
