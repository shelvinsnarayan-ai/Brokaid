"""Phase R5b-1a — daily-brief kernel (dispatch-aware).

Consolidates the non-KPI reads that `routes/daily_brief.py` needs
so the route becomes a thin dispatcher. Under Postgres the reads
preserve the pre-extraction inline SQL byte-for-byte; under Mongo
the same shape is produced from `events` + `proj_queue` collections
via `TenantSafeDatabase` under `use_tenant`.

Returned shape from `compose_brief_body()`:
    {
      "tenant_display": str | None,
      "new_matters": {"count": int, "links": [event_id, ...]},
      "endorsements_in_flight": {"count": int, "links": [...]},
      "claims_in_flight": {"count": int, "links": [...]},
      "hitl_queue_size": int,
      "overdue_hitl": int,
    }

The KPI reads stay in `kpis` / `kpis_dispatch` (R5a-2a); the route
composes both.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.tenant_safe import TenantSafeDatabase, wrap
from app.kernel.tenant_ctx import use_tenant


_ENDORSEMENT_TYPES = ("EndorsementInstructed", "EndorsementQuoted", "QuotePresented")
_CLAIM_TYPES = ("ClaimOpened", "ClaimUpdated")


def _is_mongo(store) -> bool:
    return isinstance(store, (AsyncIOMotorDatabase, TenantSafeDatabase))


async def compose_brief_body(store, tenant_id: str) -> dict[str, Any]:
    if _is_mongo(store):
        return await _compose_mongo(store, tenant_id)
    return await _compose_pg(store, tenant_id)


async def _compose_pg(pool, tenant_id: str) -> dict[str, Any]:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            t = await c.fetchrow(
                "SELECT display_name FROM tenants WHERE tenant_id = $1::uuid",
                tenant_id,
            )
            new_matters_rows = await c.fetch(
                "SELECT event_id::text AS event_id FROM events "
                " WHERE tenant_id = $1::uuid AND type = 'MatterCreated' "
                "   AND ts >= NOW() - INTERVAL '24 hours'",
                tenant_id,
            )
            endorsements_rows = await c.fetch(
                "SELECT event_id::text AS event_id FROM events "
                " WHERE tenant_id = $1::uuid AND type = ANY($2::text[]) "
                "   AND ts >= NOW() - INTERVAL '24 hours'",
                tenant_id, list(_ENDORSEMENT_TYPES),
            )
            claims_rows = await c.fetch(
                "SELECT event_id::text AS event_id FROM events "
                " WHERE tenant_id = $1::uuid AND type = ANY($2::text[]) "
                "   AND ts >= NOW() - INTERVAL '24 hours'",
                tenant_id, list(_CLAIM_TYPES),
            )
            hitl_size_row = await c.fetchrow(
                "SELECT COUNT(*) AS n FROM proj_queue "
                " WHERE tenant_id = $1::uuid AND resolved_at IS NULL",
                tenant_id,
            )
            overdue_row = await c.fetchrow(
                "SELECT COUNT(*) AS n FROM proj_queue "
                " WHERE tenant_id = $1::uuid AND resolved_at IS NULL "
                "   AND sla_deadline < NOW()",
                tenant_id,
            )
    return {
        "tenant_display": t["display_name"] if t else None,
        "new_matters": {
            "count": len(new_matters_rows),
            "links": [r["event_id"] for r in new_matters_rows],
        },
        "endorsements_in_flight": {
            "count": len(endorsements_rows),
            "links": [r["event_id"] for r in endorsements_rows],
        },
        "claims_in_flight": {
            "count": len(claims_rows),
            "links": [r["event_id"] for r in claims_rows],
        },
        "hitl_queue_size": int(hitl_size_row["n"]) if hitl_size_row else 0,
        "overdue_hitl": int(overdue_row["n"]) if overdue_row else 0,
    }


async def _compose_mongo(store, tenant_id: str) -> dict[str, Any]:
    safe = store if isinstance(store, TenantSafeDatabase) else wrap(store)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    now = datetime.now(timezone.utc)
    async with use_tenant(tenant_id):
        # Tenant display name from the system `tenants` collection.
        t_row = await safe.system("tenants").find_one({"tenant_id": tenant_id})

        async def _count_ids(types_tuple: tuple[str, ...]) -> tuple[int, list[str]]:
            ids: list[str] = []
            async for r in safe.events.find(
                {"type": {"$in": list(types_tuple)}}
            ).sort("seq", -1):
                ts = r.get("ts")
                if isinstance(ts, str):
                    try:
                        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except Exception:
                        ts = None
                if ts is None or ts < cutoff:
                    continue
                ids.append(r["event_id"])
            return len(ids), ids

        new_count, new_ids = await _count_ids(("MatterCreated",))
        endo_count, endo_ids = await _count_ids(_ENDORSEMENT_TYPES)
        claim_count, claim_ids = await _count_ids(_CLAIM_TYPES)
        hitl_size = await safe.proj_queue.count_documents({"resolved_at": None})
        overdue = await safe.proj_queue.count_documents({
            "resolved_at": None,
            "sla_deadline": {"$lt": now},
        })
    return {
        "tenant_display": t_row["display_name"] if t_row else None,
        "new_matters": {"count": new_count, "links": new_ids},
        "endorsements_in_flight": {"count": endo_count, "links": endo_ids},
        "claims_in_flight": {"count": claim_count, "links": claim_ids},
        "hitl_queue_size": int(hitl_size),
        "overdue_hitl": int(overdue),
    }
