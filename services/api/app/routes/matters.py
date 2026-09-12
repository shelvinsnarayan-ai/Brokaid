"""Operator matters — projection-driven read model.

Phase 1 auth: expects `X-Tenant-Slug` header. Real auth lands phase 4.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request

router = APIRouter(prefix="/api/o")


@router.get("/matters")
async def list_matters(
    request: Request,
    x_tenant_slug: str = Header(..., alias="X-Tenant-Slug"),
) -> dict:
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        t = await conn.fetchrow(
            "SELECT tenant_id, slug, display_name FROM tenants WHERE slug = $1",
            x_tenant_slug,
        )
        if t is None:
            raise HTTPException(status_code=404, detail=f"unknown tenant: {x_tenant_slug}")
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", str(t["tenant_id"])
            )
            rows = await conn.fetch(
                """
                SELECT matter_id::text AS matter_id,
                       client_id::text AS client_id,
                       status, subject,
                       updated_at,
                       updated_by_event::text AS updated_by_event
                  FROM proj_matters
                 WHERE tenant_id = $1
                 ORDER BY updated_at DESC
                """,
                str(t["tenant_id"]),
            )
    return {
        "tenant": {"slug": t["slug"], "display_name": t["display_name"]},
        "matters": [
            {
                "matter_id": r["matter_id"],
                "client_id": r["client_id"],
                "status": r["status"],
                "subject": r["subject"],
                "updated_at": r["updated_at"].isoformat(timespec="microseconds"),
                "updated_by_event": r["updated_by_event"],
            }
            for r in rows
        ],
    }
