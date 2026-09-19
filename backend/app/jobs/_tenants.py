"""Phase R5c-3 — dispatch-aware tenant enumeration for scheduled jobs.

Every job needs to iterate tenants (sorted by slug for determinism)
under both backends. This module returns the same `[{"tenant_id":
str, "slug": str}]` shape on Pool or Motor.
"""
from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.tenant_safe import TenantSafeDatabase, wrap as _wrap_safe


def _is_mongo(store) -> bool:
    return isinstance(store, (AsyncIOMotorDatabase, TenantSafeDatabase))


async def list_tenants(
    store, exclude_slug: str | None = None,
) -> list[dict[str, str]]:
    if _is_mongo(store):
        safe = _wrap_safe(store)
        q: dict = {}
        if exclude_slug:
            q["slug"] = {"$ne": exclude_slug}
        out: list[dict[str, str]] = []
        async for r in safe.system("tenants").find(q).sort("slug", 1):
            out.append({"tenant_id": r["tenant_id"], "slug": r["slug"]})
        return out
    pool: object = store
    async with pool.acquire() as c:
        if exclude_slug:
            rows = await c.fetch(
                "SELECT tenant_id::text AS tenant_id, slug FROM tenants "
                " WHERE slug <> $1 ORDER BY slug",
                exclude_slug,
            )
        else:
            rows = await c.fetch(
                "SELECT tenant_id::text AS tenant_id, slug FROM tenants "
                " ORDER BY slug",
            )
    return [{"tenant_id": r["tenant_id"], "slug": r["slug"]} for r in rows]


async def find_by_slug(store, slug: str) -> dict[str, str] | None:
    if _is_mongo(store):
        safe = _wrap_safe(store)
        row = await safe.system("tenants").find_one({"slug": slug})
        return {"tenant_id": row["tenant_id"], "slug": row["slug"]} if row else None
    pool: object = store
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT tenant_id::text AS tenant_id, slug FROM tenants "
            " WHERE slug = $1", slug,
        )
    return {"tenant_id": row["tenant_id"], "slug": row["slug"]} if row else None


__all__ = ["list_tenants", "find_by_slug"]
