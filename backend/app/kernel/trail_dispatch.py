"""Phase R4c — ledger append + read (R5c-4b — Mongo-only).

Thin wrapper kept so existing callers importing
`from app.kernel.trail_dispatch import append_via_dispatch, read_events`
continue to work unchanged.
"""
from __future__ import annotations

from typing import Any

from app.kernel import trail_mongo as _mongo_trail


async def append_via_dispatch(
    store, tenant_id: str, type: str,
    actor: dict, payload: dict, idempotency_key: str,
    mocked: bool = False,
) -> dict[str, Any]:
    """Return `{event_id, seq, event_hash}` — Mongo append."""
    from app.kernel.tenant_ctx import use_tenant
    async with use_tenant(tenant_id):
        ev = await _mongo_trail.append_event(
            store, tenant_id, type,
            actor=actor, payload=payload,
            idempotency_key=idempotency_key,
            mocked=mocked,
        )
    return {
        "event_id": ev.event_id, "seq": ev.seq,
        "event_hash": ev.event_hash,
    }


async def tip_hash(store, tenant_id: str) -> str | None:
    """Return the current chain tip's event_hash for a tenant, or None."""
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import use_tenant
    safe_db = wrap(store)
    async with use_tenant(tenant_id):
        row = await safe_db.events.find_one({}, sort=[("seq", -1)])
    return row["event_hash"] if row else None


async def read_events(
    store, tenant_ref: str,
    from_seq: int | None = None, to_seq: int | None = None,
):
    """Read a tenant + its events. `tenant_ref` = slug OR tenant_id UUID."""
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import use_tenant
    safe = wrap(store)
    q = ({"tenant_id": tenant_ref}
         if _mongo_trail._UUID_RE.match(tenant_ref)
         else {"slug": tenant_ref})
    t_doc = await safe.system("tenants").find_one(q)
    if t_doc is None:
        raise LookupError(f"unknown tenant: {tenant_ref}")
    tid = t_doc["tenant_id"]
    async with use_tenant(tid):
        return await _mongo_trail.read_events(
            store, tenant_ref, from_seq=from_seq, to_seq=to_seq,
        )
