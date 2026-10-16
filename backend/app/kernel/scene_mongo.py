"""Phase R5a-2b-β — scene kernel Mongo readers.

Sibling to `kernel/scene.py`. Only the read side lives here — writes
go through the shared `trail_dispatch.append_via_dispatch` choke-
point (which routes to `trail_mongo.append_event` under Mongo),
matching the pattern established in R5a-2b-α for the placement
kernel.

All reads flow through `TenantSafeDatabase` under an active
`use_tenant(tid)` context so tenant isolation is enforced at the
wrapper boundary. `TenantMismatchError` on any caller-supplied
`tenant_id` that contradicts the bound context.
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


async def load_policy(
    db: AsyncIOMotorDatabase | TenantSafeDatabase,
    tenant_id: str, policy_id: str,
) -> dict[str, Any] | None:
    safe = _wrap(db)
    async with use_tenant(tenant_id):
        row = await safe.policies.find_one({"policy_id": policy_id})
    if not row:
        return None
    return {
        "policy_id": row["policy_id"],
        "client_id": row["client_id"],
        "product": row.get("product"),
        "schedule": _parse(row.get("schedule") or {}),
        "premium_amount": str(row.get("premium_amount") or "0"),
        "premium_currency": row.get("premium_currency"),
        "insurer_display": row.get("insurer_display"),
        "status": row.get("status"),
    }


async def last_event_id_for_envelope(
    db: AsyncIOMotorDatabase | TenantSafeDatabase,
    tenant_id: str, type_: str, envelope_id: str,
) -> str | None:
    safe = _wrap(db)
    async with use_tenant(tenant_id):
        async for r in safe.events.find({
            "type": type_,
        }).sort("seq", -1):
            p = r.get("payload") or {}
            if p.get("envelope_id") == envelope_id:
                return r["event_id"]
    return None


async def list_recent_events_for_policy(
    db: AsyncIOMotorDatabase | TenantSafeDatabase,
    tenant_id: str, policy_id: str, limit: int = 40,
) -> list[dict[str, Any]]:
    safe = _wrap(db)
    out: list[dict[str, Any]] = []
    async with use_tenant(tenant_id):
        cursor = safe.events.find({}).sort("seq", -1)
        async for r in cursor:
            p = r.get("payload") or {}
            if p.get("policy_id") != policy_id:
                continue
            out.append({"type": r["type"], "payload": _parse(p), "seq": r.get("seq")})
            if len(out) >= limit:
                break
    return out


async def envelope_events_without_policy(db, tenant_id: str, envelope_id: str) -> list[dict[str, Any]]:
    """Envelope events whose payload has no policy_id (e.g. OperatorApproval on a review card)."""
    safe = _wrap(db)
    async with use_tenant(tenant_id):
        return [{"type": r["type"], "payload": _parse(r.get("payload") or {}), "seq": r.get("seq")}
                async for r in safe.events.find({"payload.envelope_id": envelope_id,
                                                 "payload.policy_id": {"$exists": False}})]


async def find_carrier_quote(
    db: AsyncIOMotorDatabase | TenantSafeDatabase,
    tenant_id: str, envelope_id: str,
) -> dict[str, Any] | None:
    safe = _wrap(db)
    async with use_tenant(tenant_id):
        async for r in safe.events.find({
            "type": "CarrierQuoted",
        }).sort("seq", -1):
            p = r.get("payload") or {}
            if p.get("envelope_id") == envelope_id:
                return _parse(p)
    return None


async def find_client_jurisdiction(
    db: AsyncIOMotorDatabase | TenantSafeDatabase,
    tenant_id: str, client_id: str,
) -> str | None:
    safe = _wrap(db)
    async with use_tenant(tenant_id):
        row = await safe.clients.find_one({"client_id": client_id})
    if not row:
        return None
    return row.get("jurisdiction_code")
