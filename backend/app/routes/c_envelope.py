"""Per-envelope audit-trail endpoints (R1 #2 tester follow-up).

Exposes the ledger chain for a single envelope in a redacted form so
SIRC audiences and the ledger DoD can see actors, gate reasons, and
event hashes without touching raw payloads.

    GET /api/c/envelope/{envelope_id}/events
        Ordered array of the envelope's events. Each row:
          {seq, type, actor:{kind, ref}, mocked, gate_reason, ts,
           event_hash, prev_hash}

    GET /api/c/envelope/{envelope_id}/events/verify
        Wraps the tenant-wide chain-verify job:
          {ok: bool, break_at?: seq, envelope_seq_range?: [lo, hi]}

Both endpoints are tenant-scoped via the client session; readers get
their own tenant's events only.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_session


router = APIRouter(prefix="/api/c/envelope")


def _iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    try:
        return v.isoformat(timespec="microseconds")
    except Exception:
        return str(v)


def _redact(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload") or {}
    actor = row.get("actor") or {}
    return {
        "seq": row.get("seq"),
        "type": row.get("type"),
        "actor": {
            "kind": actor.get("kind"),
            "ref": actor.get("name") or actor.get("id"),
        },
        "mocked": bool(row.get("mocked") or payload.get("mocked")),
        "gate_reason": payload.get("gate_reason"),
        "ts": _iso(row.get("ts")),
        "event_hash": row.get("event_hash"),
        "prev_hash": row.get("prev_hash"),
    }


@router.get("/{envelope_id}/events")
async def envelope_events(
    envelope_id: str,
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    """Return the ordered event chain for an envelope, redacted."""
    if len(envelope_id) < 20 or len(envelope_id) > 100:
        raise HTTPException(status_code=404, detail="envelope_not_found")
    store = request.app.state.mongo_db
    safe = wrap(store)
    rows: list[dict[str, Any]] = []
    async with use_tenant(session.tenant_id):
        async for r in safe.events.find(
            {"payload.envelope_id": envelope_id}
        ).sort("seq", 1):
            rows.append(_redact(r))
    if not rows:
        raise HTTPException(status_code=404, detail="envelope_not_found")
    return {
        "envelope_id": envelope_id,
        "count": len(rows),
        "events": rows,
    }


async def _load_tenant_slug(store, tenant_id: str) -> str | None:
    safe = wrap(store)
    async with use_tenant(tenant_id):
        row = await safe.system("tenants").find_one({"tenant_id": tenant_id})
    return row.get("slug") if row else None


@router.get("/{envelope_id}/events/verify")
async def envelope_events_verify(
    envelope_id: str,
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    """Tenant-wide chain-verify wrapped for a single envelope.

    Runs the same verifier the boot job uses. Returns:
      { ok: bool, break_at?: seq, envelope_seq_range: [lo, hi] }
    """
    if len(envelope_id) < 20 or len(envelope_id) > 100:
        raise HTTPException(status_code=404, detail="envelope_not_found")
    store = request.app.state.mongo_db
    safe = wrap(store)

    lo: int | None = None
    hi: int | None = None
    async with use_tenant(session.tenant_id):
        async for r in safe.events.find(
            {"payload.envelope_id": envelope_id}, {"seq": 1}
        ).sort("seq", 1):
            seq = r.get("seq")
            if seq is None:
                continue
            if lo is None:
                lo = seq
            hi = seq
    if lo is None:
        raise HTTPException(status_code=404, detail="envelope_not_found")

    slug = await _load_tenant_slug(store, session.tenant_id)
    if slug is None:
        raise HTTPException(status_code=500, detail="tenant_slug_missing")

    from app.kernel.trail_dispatch import read_events
    from app.kernel.verifier import verify_events

    _, events = await read_events(store, slug)
    payload = [
        {"event_id": e.event_id, "tenant_id": e.tenant_id, "seq": e.seq,
         "ts": e.ts, "actor": e.actor, "type": e.type,
         "payload": e.payload, "payload_jcs": e.payload_jcs,
         "prev_hash": e.prev_hash, "event_hash": e.event_hash,
         "idempotency_key": e.idempotency_key, "mocked": e.mocked}
        for e in events
    ]
    result = verify_events(payload)
    body: dict[str, Any] = {
        "ok": bool(result.ok),
        "envelope_seq_range": [lo, hi],
    }
    if not result.ok:
        body["break_at"] = result.failure_seq
    return body
