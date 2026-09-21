"""GET /api/c/next-action — client-facing "one thing to do next".

Returns the single most-relevant pending envelope for the logged-in
client. Composed live so it surfaces real request-change flows as
well as the seeded burmac demo envelope. Suppresses itself once
`EndorsementExecuted` lands.

Ordering: newest signal first. Sources scanned (highest priority
last so the loop keeps the most recent one):
  1. Seeded `AgentOutputProduced` with `class_key=endorsement_awaiting_client_approval`
  2. Live `EndorsementQuoteReady` (from POST /request-change)
  3. Fresh `CertificateRegenerated` (< 6h old, post-execution) — shown
     as "certificate ready" until the client visits it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_session


router = APIRouter(prefix="/api/c")


CERT_FRESH_WINDOW = timedelta(hours=6)


def _iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    try:
        return v.isoformat(timespec="microseconds")
    except Exception:
        return str(v)


async def _client_policy_ids(safe, client_id: str) -> set[str]:
    ids: set[str] = set()
    async for r in safe.policies.find({"client_id": client_id}, {"policy_id": 1}):
        pid = r.get("policy_id")
        if pid:
            ids.add(pid)
    return ids


SIGNAL_TYPES = (
    "CertificateRegenerated",
    "EndorsementExecuted",
    "EndorsementQuoteReady",
)


async def _lookup(store, session: Session) -> dict[str, Any] | None:
    """Return the freshest actionable signal for the client.

    Iterates events seq-DESC filtered to the client's policies and picks
    the first row that maps to a rendered CTA. "Newest wins" — so if
    the client just executed an endorsement (CertificateRegenerated
    lands after EndorsementQuoteReady in the ledger) the cert-ready
    card takes precedence over any stale unexecuted quote.
    """
    if not session.client_id:
        return None
    safe = wrap(store)
    async with use_tenant(session.tenant_id):
        policy_ids = await _client_policy_ids(safe, session.client_id)

        # Scan the newest events across our three signal types. Stop at
        # the first one that maps to a card.
        async for r in safe.events.find(
            {"type": {"$in": list(SIGNAL_TYPES)}}
        ).sort("seq", -1):
            p = r.get("payload") or {}
            if p.get("policy_id") not in policy_ids:
                continue
            t = r.get("type")

            if t == "CertificateRegenerated":
                # Only surface certificates issued within the fresh
                # window — older ones are just historical and shouldn't
                # nag the client.
                now = datetime.now(timezone.utc)
                issued_iso = _iso(p.get("effective_date"))
                if issued_iso:
                    try:
                        issued_dt = datetime.fromisoformat(
                            issued_iso.replace("Z", "+00:00")
                        )
                        if issued_dt.tzinfo is None:
                            issued_dt = issued_dt.replace(tzinfo=timezone.utc)
                        if (now - issued_dt) > CERT_FRESH_WINDOW:
                            continue
                    except Exception:
                        continue
                return {
                    "kind": "certificate_ready",
                    "envelope_id": p.get("envelope_id"),
                    "policy_id": p.get("policy_id"),
                    "certificate_id": p.get("certificate_id"),
                    "summary": "Updated certificate ready",
                    "revised_premium_aud": None,
                    "delta_aud": None,
                    "href": (
                        f"/c/cover/{p.get('policy_id')}/certificate/{p.get('envelope_id')}"
                        if p.get("envelope_id") and p.get("policy_id")
                        else f"/c/cover/{p.get('policy_id')}/certificate"
                    ),
                    "mocked": True,
                }

            if t == "EndorsementExecuted":
                # Executed but no certificate yet — the client shouldn't
                # be prompted to review this envelope. Skip and keep
                # looking further back for anything else actionable.
                continue

            if t == "EndorsementQuoteReady":
                env_id = p.get("envelope_id")
                # Skip if this envelope has been executed later in the
                # chain (means the QuoteReady is historical).
                executed = await safe.events.find_one({
                    "type": "EndorsementExecuted",
                    "payload.envelope_id": env_id,
                })
                if executed is not None:
                    continue
                cq = await safe.events.find_one({
                    "type": "CarrierQuoted",
                    "payload.envelope_id": env_id,
                })
                cq_payload = (cq or {}).get("payload") or {}
                return {
                    "kind": "endorsement_quote_ready",
                    "envelope_id": env_id,
                    "policy_id": p.get("policy_id"),
                    "summary": (
                        f"Quote ready — {cq_payload.get('premium_currency','AUD')} "
                        f"{cq_payload.get('premium_change_amount','0')} uplift"
                        if cq_payload else "Quote ready to review"
                    ),
                    "revised_premium_aud": None,
                    "delta_aud": (
                        float(cq_payload.get("premium_change_amount") or 0) or None
                    ),
                    "href": f"/c/cover/{p.get('policy_id')}/pending",
                    "mocked": True,
                }

        # Fallback: seeded static "endorsement_awaiting_client_approval"
        # (kept for the burmac demo seed which pre-dates the live chain).
        seeded = await safe.events.find_one(
            {
                "type": "AgentOutputProduced",
                "payload.client_id": session.client_id,
                "payload.class_key": "endorsement_awaiting_client_approval",
            },
            sort=[("ts", -1)],
        )
        if seeded:
            p = seeded.get("payload") or {}
            env_id = p.get("envelope_id")
            executed = None
            if env_id:
                executed = await safe.events.find_one({
                    "type": "EndorsementExecuted",
                    "payload.envelope_id": env_id,
                })
            if executed is None:
                fields = (p.get("output") or {}).get("fields") or {}
                return {
                    "kind": p.get("class_key"),
                    "envelope_id": env_id,
                    "policy_id": p.get("policy_id"),
                    "summary": (p.get("output") or {}).get("summary"),
                    "revised_premium_aud": fields.get("revised_premium_aud"),
                    "delta_aud": fields.get("delta_aud"),
                    "href": (
                        f"/c/cover/{p.get('policy_id')}/pending"
                        if p.get("policy_id") else None
                    ),
                    "mocked": True,
                }

    return None


@router.get("/next-action")
async def next_action(
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    payload = await _lookup(request.app.state.mongo_db, session)
    return {"next_action": payload}
