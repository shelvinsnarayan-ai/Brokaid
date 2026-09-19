"""Phase R5b-1a — Mongo mirror of endorsement lineage + schedule
read-modify-write. Writes go through the shared trail_dispatch
choke-point (which routes to trail_mongo.append_event), matching
the R5a-2b-β pattern for the scene kernel.

All reads flow through `TenantSafeDatabase` under an active
`use_tenant(tid)` context so tenant isolation is enforced at the
wrapper boundary.
"""
from __future__ import annotations

import json
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.tenant_safe import TenantSafeDatabase, wrap
from app.kernel.tenant_ctx import use_tenant


def _wrap(db: AsyncIOMotorDatabase | TenantSafeDatabase) -> TenantSafeDatabase:
    return db if isinstance(db, TenantSafeDatabase) else wrap(db)


def _parse(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return {}
    return {}


async def load_lineage(
    db: AsyncIOMotorDatabase | TenantSafeDatabase,
    tenant_id: str, envelope_id: str,
) -> dict[str, Any]:
    """Mongo mirror of endorsement._load_lineage on PG. Returns
    {event_type: first_payload_dict} scanning `events` ordered by
    seq ASC and filtering by payload.envelope_id (same as the PG
    inline SELECT)."""
    safe = _wrap(db)
    lineage: dict[str, Any] = {}
    async with use_tenant(tenant_id):
        async for r in safe.events.find({}).sort("seq", 1):
            p = r.get("payload") or {}
            if p.get("envelope_id") != envelope_id:
                continue
            t = r["type"]
            if t not in lineage:
                lineage[t] = _parse(p)
    return lineage


async def rmw_policy_schedule(
    db: AsyncIOMotorDatabase | TenantSafeDatabase,
    tenant_id: str, policy_id: str, changes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Read schedule → apply changes locally → write back. Non-atomic
    across processes but sufficient here (endorsement is serialised
    per-policy on the operator queue upstream)."""
    from app.kernel.endorsement import _apply_change, EndorsementLineageIncomplete
    safe = _wrap(db)
    async with use_tenant(tenant_id):
        row = await safe.policies.find_one({"policy_id": policy_id})
        if row is None:
            raise EndorsementLineageIncomplete(f"policy {policy_id} not found")
        schedule = row.get("schedule") or {}
        schedule = _parse(schedule)
        for ch in changes:
            _apply_change(schedule, ch)
        await safe.policies.update_one(
            {"policy_id": policy_id},
            {"$set": {"schedule": schedule}},
        )
    return schedule
