"""Client 360 kernel — R5c-1b-β.

Extracted from `routes/client_360.py` (3 endpoints). PG paths byte-
verbatim from the pre-extraction route; Mongo paths use
`TenantSafeDatabase` under `use_tenant(tid)`.

Three read helpers:
- `list_all_clients_with_policy_counts` — for GET /api/o/clients.
- `get_client_header` — for GET /api/o/client/{id} (returns client row
  + active_policies count + open_matters count).
- `get_client_timeline` — for GET /api/o/client/{id}/timeline.
"""
from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.tenant_safe import TenantSafeDatabase, wrap as _wrap_safe
from app.kernel.tenant_ctx import use_tenant


def _is_mongo(store) -> bool:
    return isinstance(store, (AsyncIOMotorDatabase, TenantSafeDatabase))


async def list_all_clients_with_policy_counts(
    store, tenant_id: str,
) -> list[dict]:
    if _is_mongo(store):
        safe = _wrap_safe(store)
        out: list[dict] = []
        async with use_tenant(tenant_id):
            async for c in safe.clients.find({"test": {"$ne": True}}).sort("display_name", 1):
                count = 0
                async for _ in safe.policies.find({"client_id": c["client_id"]}):
                    count += 1
                out.append({
                    "client_id": c["client_id"],
                    "display_name": c.get("display_name"),
                    "industry": c.get("industry"),
                    "domicile": c.get("domicile"),
                    "jurisdiction_code": c.get("jurisdiction_code"),
                    "policy_count": count,
                })
        return out
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            rows = await c.fetch(
                """
                SELECT cl.client_id::text AS client_id, cl.display_name,
                       cl.industry, cl.domicile, cl.jurisdiction_code,
                       COUNT(p.policy_id) AS policy_count
                  FROM clients cl
                  LEFT JOIN policies p
                    ON p.tenant_id = cl.tenant_id AND p.client_id = cl.client_id
                 WHERE cl.tenant_id = $1::uuid
                 GROUP BY cl.client_id
                 ORDER BY cl.display_name ASC
                """,
                tenant_id,
            )
    return [dict(r) for r in rows]


async def get_client_header(
    store, tenant_id: str, client_id: str,
) -> dict | None:
    if _is_mongo(store):
        safe = _wrap_safe(store)
        async with use_tenant(tenant_id):
            client_row = await safe.clients.find_one({"client_id": client_id})
            if not client_row:
                return None
            active_policies = 0
            async for _ in safe.policies.find({
                "client_id": client_id,
                "status": {"$in": ["managed", "tracked"]},
            }):
                active_policies += 1
            open_matters = 0
            async for _ in safe.proj_matters.find({
                "client_id": client_id,
                "status": {"$ne": "closed"},
            }):
                open_matters += 1
        return {
            "client_id": client_row["client_id"],
            "display_name": client_row.get("display_name"),
            "industry": client_row.get("industry"),
            "domicile": client_row.get("domicile"),
            "jurisdiction_code": client_row.get("jurisdiction_code"),
            "active_policies": int(active_policies),
            "open_matters": int(open_matters),
        }
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            client_row = await c.fetchrow(
                "SELECT client_id::text AS client_id, display_name, industry, "
                "       domicile, jurisdiction_code "
                "  FROM clients WHERE tenant_id = $1::uuid AND client_id = $2::uuid",
                tenant_id, client_id,
            )
            if not client_row:
                return None
            active_policies = await c.fetchval(
                "SELECT COUNT(*) FROM policies "
                " WHERE tenant_id = $1::uuid AND client_id = $2::uuid "
                "   AND status IN ('managed','tracked')",
                tenant_id, client_id,
            )
            open_matters = await c.fetchval(
                "SELECT COUNT(*) FROM proj_matters "
                " WHERE tenant_id = $1::uuid AND client_id = $2::uuid "
                "   AND status <> 'closed'",
                tenant_id, client_id,
            )
    return {
        **dict(client_row),
        "active_policies": int(active_policies or 0),
        "open_matters": int(open_matters or 0),
    }


async def get_client_timeline(
    store, tenant_id: str, client_id: str,
    *, limit: int = 50, offset: int = 0,
) -> tuple[int, list[dict]]:
    if _is_mongo(store):
        safe = _wrap_safe(store)
        events: list[dict] = []
        total = 0
        async with use_tenant(tenant_id):
            query = {"$or": [
                {"payload.client_id": client_id},
                {"actor.id": client_id},
            ]}
            async for _ in safe.events.find(query):
                total += 1
            async for r in safe.events.find(query).sort("seq", -1).skip(offset).limit(limit):
                events.append({
                    "event_id": r["event_id"],
                    "seq": int(r["seq"]),
                    "ts": r["ts"], "type": r["type"],
                    "actor": r["actor"], "payload": r["payload"],
                })
        return total, events
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            total = await c.fetchval(
                "SELECT COUNT(*) FROM events "
                " WHERE tenant_id = $1::uuid "
                "   AND (payload->>'client_id' = $2 OR actor->>'id' = $2)",
                tenant_id, client_id,
            )
            rows = await c.fetch(
                """
                SELECT event_id::text AS event_id, seq, ts, type, actor, payload
                  FROM events
                 WHERE tenant_id = $1::uuid
                   AND (payload->>'client_id' = $2 OR actor->>'id' = $2)
                 ORDER BY seq DESC
                 LIMIT $3 OFFSET $4
                """,
                tenant_id, client_id, limit, offset,
            )
    return int(total or 0), [dict(r) for r in rows]
