"""Operator matters — projection-driven read model (R5c-4b — Mongo-only)."""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request

from app.db.tenant_safe import wrap as _wrap_safe
from app.kernel.tenant_ctx import use_tenant

router = APIRouter(prefix="/api/o")


def _store(request: Request):
    s = getattr(request.app.state, "mongo_db", None)
    if s is None:
        raise HTTPException(status_code=503, detail="mongo_store_unavailable")
    return s


async def _resolve_tenant(store, slug: str) -> dict | None:
    safe = _wrap_safe(store)
    row = await safe.system("tenants").find_one({"slug": slug})
    if row is None:
        return None
    return {
        "tenant_id": row["tenant_id"],
        "slug": row["slug"],
        "display_name": row["display_name"],
    }


async def _list_matters(store, tenant_id: str) -> list[dict]:
    safe = _wrap_safe(store)
    out: list[dict] = []
    async with use_tenant(tenant_id):
        cursor = safe.proj_matters.find({}).sort("updated_at", -1)
        async for r in cursor:
            ts = r["updated_at"]
            out.append({
                "matter_id": r["matter_id"],
                "client_id": r["client_id"],
                "status": r.get("status"),
                "subject": r.get("subject"),
                "updated_at": (
                    ts.isoformat(timespec="microseconds")
                    if hasattr(ts, "isoformat") else str(ts)
                ),
                "updated_by_event": r.get("updated_by_event"),
            })
    return out


@router.get("/matters")
async def list_matters(
    request: Request,
    x_tenant_slug: str = Header(..., alias="X-Tenant-Slug"),
) -> dict:
    store = _store(request)
    t = await _resolve_tenant(store, x_tenant_slug)
    if t is None:
        raise HTTPException(
            status_code=404, detail=f"unknown tenant: {x_tenant_slug}",
        )
    rows = await _list_matters(store, t["tenant_id"])
    return {
        "tenant": {"slug": t["slug"], "display_name": t["display_name"]},
        "matters": rows,
    }
