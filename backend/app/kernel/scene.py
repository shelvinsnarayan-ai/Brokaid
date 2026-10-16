"""Phase R5a-2b-β — scene kernel module (dispatch-aware).

Extract-first discipline: this module is a verbatim move of the
five inline helpers + nine `trail.append` sites and one pure
state-computation block from `routes/c_scene.py`. No behavioural
change — same event types, same actor shapes, same payload dict
literals, same idempotency_key patterns, same mocked flags.

Two intentional variances from the placement kernel (`kernel/
placement.py`) — both preserved verbatim from `c_scene.py`:

- `emit_carrier_quoted` here spreads `quote` into the payload
  (`payload = {envelope_id, policy_id, **quote}`) whereas the
  placement kernel picks specific keys AND lifts `mocked: True`
  into the payload. Both landing sites remain distinct event
  emitters — the scene kernel MUST NOT be plumbed into the
  placement route or vice-versa without a re-audit of the payload
  keyset (a byte-identical hash chain is a keyset-sensitive
  guarantee).
- `emit_endorsement_quote_ready` here omits `intake_envelope_id`
  from the payload (the client-scene lineage has one envelope
  spanning intake + placement, so there is no separate intake
  envelope to reference) whereas the placement kernel includes it.

Under Postgres store: reads use inline SQL under a session-local
`SET app.tenant_id` inside a transaction so the RLS policy on
`events` / `policies` / `clients` can evaluate. Writes go through
`trail_dispatch.append_via_dispatch` → `trail.append` — byte-
identical event_hash preserved.

Under Mongo store: reads are delegated to `kernel.scene_mongo` (a
sibling module carrying the same public reader signatures against
`TenantSafeDatabase`). Writes go through `trail_dispatch` → Mongo
`append_event` — same JCS canonicalisation, byte-identical hash.
"""
from __future__ import annotations

import json
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.tenant_safe import TenantSafeDatabase
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch

PAYMENT_ADVISED = "payment.advised"
NEEDS_HUMAN_REVIEW = "RequestNeedsHumanReview"
ENVELOPE_ABANDONED = "EnvelopeAbandoned"
ADVICE_CLASSIFIED = "AdviceBoundaryClassified"


async def emit_advice_boundary_classified(
    store, *, tenant_id: str, envelope_id: str, policy_id: str, verdict: dict[str, Any],
) -> dict[str, Any]:
    return await _emit(
        store, tenant_id, type=ADVICE_CLASSIFIED,
        actor={"kind": "system", "id": "advice_boundary", "name": "advice-boundary"},
        payload={"envelope_id": envelope_id, "policy_id": policy_id, **verdict},
        idempotency_key=f"advice:classified:{envelope_id}",
    )


async def emit_request_needs_human_review(
    store, *, tenant_id: str, envelope_id: str, policy_id: str,
    stage: str, mismatch: dict[str, Any],
) -> dict[str, Any]:
    return await _emit(
        store, tenant_id, type=NEEDS_HUMAN_REVIEW,
        actor={"kind": "system", "id": "policy_field_guard", "name": "guard"},
        payload={"envelope_id": envelope_id, "policy_id": policy_id,
                 "stage": stage, "mismatch": mismatch,
                 "state": "awaiting_broker_review"},
        idempotency_key=f"guard:needs_review:{envelope_id}",
    )


def _is_mongo(store) -> bool:
    return isinstance(store, (AsyncIOMotorDatabase, TenantSafeDatabase))


def _parse_json_value(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return {}
    return {}


# ---- Read side --------------------------------------------------------
async def load_policy(store, tenant_id: str, policy_id: str) -> dict | None:
    """Return the policy dict for a client-side scene load.

    Same fields as the pre-extraction `_load_policy` in c_scene.py:
      policy_id, client_id, product, schedule, premium_amount,
      premium_currency, insurer_display, status
    """
    if _is_mongo(store):
        from app.kernel import scene_mongo
        return await scene_mongo.load_policy(store, tenant_id, policy_id)
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            row = await c.fetchrow(
                "SELECT policy_id::text, client_id::text, product, schedule, "
                "       premium_amount::text, premium_currency, insurer_display, "
                "       inception, expiry, status "
                "  FROM policies "
                " WHERE tenant_id=$1::uuid AND policy_id=$2::uuid",
                tenant_id, policy_id,
            )
    if not row:
        return None
    return {
        "policy_id": row["policy_id"],
        "client_id": row["client_id"],
        "product": row["product"],
        "schedule": _parse_json_value(row["schedule"]),
        "premium_amount": row["premium_amount"],
        "premium_currency": row["premium_currency"],
        "insurer_display": row["insurer_display"],
        "status": row["status"],
    }


async def last_event_id_for_envelope(
    store, tenant_id: str, type_: str, envelope_id: str,
) -> str | None:
    """Return the most-recent event_id of `type_` scoped to
    `envelope_id`, or None. Preserves the pre-extraction
    `_last_event_id` helper in c_scene.py."""
    if _is_mongo(store):
        from app.kernel import scene_mongo
        return await scene_mongo.last_event_id_for_envelope(
            store, tenant_id, type_, envelope_id,
        )
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            row = await c.fetchrow(
                "SELECT event_id::text FROM events "
                " WHERE tenant_id=$1::uuid AND type=$2 "
                "   AND payload->>'envelope_id' = $3 "
                " ORDER BY seq DESC LIMIT 1",
                tenant_id, type_, envelope_id,
            )
    return row["event_id"] if row else None


async def list_recent_events_for_policy(
    store, tenant_id: str, policy_id: str, limit: int = 40,
) -> list[dict[str, Any]]:
    """Return up to `limit` recent events under `policy_id` in
    seq-DESC order. Each row has keys {type, payload}. Mirrors the
    pre-extraction inline SELECT in c_scene.status."""
    if _is_mongo(store):
        from app.kernel import scene_mongo
        return await scene_mongo.list_recent_events_for_policy(
            store, tenant_id, policy_id, limit,
        )
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            rows = await c.fetch(
                """
                SELECT type, payload FROM events
                 WHERE tenant_id=$1::uuid
                   AND payload->>'policy_id' = $2
                 ORDER BY seq DESC
                 LIMIT %d
                """ % int(limit),
                tenant_id, policy_id,
            )
    return [
        {"type": r["type"], "payload": _parse_json_value(r["payload"])}
        for r in rows
    ]


async def find_carrier_quote(
    store, tenant_id: str, envelope_id: str,
) -> dict[str, Any] | None:
    """Latest CarrierQuoted payload for an envelope, or None."""
    if _is_mongo(store):
        from app.kernel import scene_mongo
        return await scene_mongo.find_carrier_quote(
            store, tenant_id, envelope_id,
        )
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            row = await c.fetchrow(
                "SELECT payload FROM events "
                " WHERE tenant_id=$1::uuid AND type='CarrierQuoted' "
                "   AND payload->>'envelope_id' = $2 "
                " ORDER BY seq DESC LIMIT 1",
                tenant_id, envelope_id,
            )
    return _parse_json_value(row["payload"]) if row else None


async def find_client_jurisdiction(
    store, tenant_id: str, client_id: str,
) -> str | None:
    """Return the `jurisdiction_code` for a client, or None."""
    if _is_mongo(store):
        from app.kernel import scene_mongo
        return await scene_mongo.find_client_jurisdiction(
            store, tenant_id, client_id,
        )
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            row = await c.fetchrow(
                "SELECT jurisdiction_code FROM clients "
                "WHERE tenant_id=$1::uuid AND client_id=$2::uuid",
                tenant_id, client_id,
            )
    return row["jurisdiction_code"] if row else None


# ---- Pure state-machine ----------------------------------------------
def compute_status(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the scene status body from a seq-DESC event list.

    Pure function — extracted verbatim from the tail of the
    pre-extraction `c_scene.status`. Expects each event dict to
    carry `type` (str) + `payload` (dict). Order matters only for
    the `envelope_id` field which reflects the newest event's
    envelope.

    Returns keys: state, envelope_id, recent_types (up to 10),
    quote_id, certificate, verifier_token_hash.
    """
    latest_types: list[str] = []
    payloads: dict[str, dict[str, Any]] = {}
    envelope_id: str | None = None
    for r in events:
        p = r.get("payload") or {}
        if envelope_id is None:
            envelope_id = p.get("envelope_id")
        payloads.setdefault(r["type"], p)
        latest_types.append(r["type"])
    newest_env_types = [
        r["type"] for r in events
        if (r.get("payload") or {}).get("envelope_id") == envelope_id
    ]
    state = "idle"
    # R0.11 follow-up: state follows the newest envelope only (older envelopes' signature/payment
    # events were leaking into a fresh request). Events without an envelope_id still count.
    env_t = set(newest_env_types) | {
        r["type"] for r in events if not (r.get("payload") or {}).get("envelope_id")}
    declined = any(
        r["type"] == ADVICE_CLASSIFIED and (r.get("payload") or {}).get("tier") == "X"
        for r in events if (r.get("payload") or {}).get("envelope_id") == envelope_id
    )
    if ENVELOPE_ABANDONED in newest_env_types:
        state = "abandoned"
    elif declined:
        state = "declined"
    elif (NEEDS_HUMAN_REVIEW in newest_env_types
          and "OperatorApproval" not in newest_env_types):
        state = "awaiting_broker_review"
    elif (NEEDS_HUMAN_REVIEW in newest_env_types
          and "EndorsementQuoteReady" not in newest_env_types):
        state = "broker_reviewed"  # broker took the parked request; they follow up directly
    elif "CertificateRegenerated" in env_t:
        state = "certificate_ready"
    elif "EndorsementExecuted" in env_t:
        state = "executing"
    elif "SignatureCompleted" in env_t:
        state = "executing"
    elif "SignatureRequested" in env_t:
        state = "awaiting_signature"
    elif PAYMENT_ADVISED in env_t:
        state = "awaiting_signature"
    elif "Approval" in env_t:
        state = "awaiting_payment"
    elif "EndorsementQuoteReady" in env_t:
        state = "awaiting_operator"
    elif "SubmissionDrafted" in env_t:
        state = "placement_running"
    elif "ClientChangeRequested" in env_t:
        state = "intake_running"
    return {
        "state": state,
        "envelope_id": envelope_id,
        "recent_types": latest_types[:10],
        "quote_id": (payloads.get("CarrierQuoted") or {}).get("quote_id"),
        "certificate": (payloads.get("CertificateRegenerated") or {}).get(
            "certificate"
        ),
        "verifier_token_hash": (
            (payloads.get("VerifierTokenIssued") or {}).get("token_hash")
        ),
    }


# ---- Write side — 9 emit_* helpers (extract-first verbatim) ----------
async def _emit(store, tenant_id: str, **kw) -> dict[str, Any]:
    if _is_mongo(store):
        async with use_tenant(tenant_id):
            return await append_via_dispatch(store, tenant_id, **kw)
    return await append_via_dispatch(store, tenant_id, **kw)


async def emit_client_change_requested(
    store, *, tenant_id: str, user_id: str, envelope_id: str,
    policy_id: str, client_id: str, raw_message: str,
    client_idempotency_key: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "envelope_id": envelope_id,
        "policy_id": policy_id,
        "client_id": client_id,
        "raw_message": raw_message,
        "submitted_by": user_id,
    }
    if client_idempotency_key:
        payload["client_idempotency_key"] = client_idempotency_key
    # Scope the trail-level idempotency key on the client-supplied
    # nonce (if provided) so Mongo's unique index on events.
    # idempotency_key serialises rapid-fire resubmits atomically. Three
    # taps in <5ms all land here with the same key; the first wins and
    # the rest raise DuplicateIdempotencyKey.
    idem_key = (
        f"cchreq:{tenant_id}:{policy_id}:{user_id}:{client_idempotency_key}"
        if client_idempotency_key
        else f"cchreq:{envelope_id}"
    )
    return await _emit(
        store, tenant_id, type="ClientChangeRequested",
        actor={"kind": "human", "id": user_id, "name": "client_owner"},
        payload=payload,
        idempotency_key=idem_key,
    )


async def emit_submission_drafted(
    store, *, tenant_id: str, envelope_id: str, policy_id: str,
    submission_summary: str | None,
    structured_submission: dict[str, Any],
    recommended_wording_ref: str | None,
) -> dict[str, Any]:
    """Scene variant — same payload shape as the placement kernel's
    `emit_submission_drafted`. Kept as a scene-local emitter so the
    scene surface is self-contained."""
    return await _emit(
        store, tenant_id, type="SubmissionDrafted",
        actor={"kind": "system", "id": "placement", "name": "placement"},
        payload={
            "envelope_id": envelope_id,
            "submission_summary": submission_summary,
            "structured_submission": structured_submission or {},
            "recommended_wording_ref": recommended_wording_ref,
            "policy_id": policy_id,
            "mocked": False,
        },
        idempotency_key=f"placement:submission:{envelope_id}",
    )


async def emit_carrier_quoted(
    store, *, tenant_id: str, envelope_id: str, policy_id: str,
    quote: dict[str, Any],
) -> dict[str, Any]:
    """Scene variant — payload spreads the full quote dict, mocked
    flag lives on the outer append kwarg (and inside quote via the
    mock adapter). DISTINCT from `placement.emit_carrier_quoted`."""
    return await _emit(
        store, tenant_id, type="CarrierQuoted",
        actor={"kind": "system", "id": "carrier_channel", "name": "carrier"},
        payload={
            "envelope_id": envelope_id,
            "policy_id": policy_id,
            **quote,
        },
        mocked=True,
        idempotency_key=f"carrier:quote:{envelope_id}",
    )


async def emit_endorsement_quote_ready(
    store, *, tenant_id: str, envelope_id: str, policy_id: str,
    quote_id: str,
) -> dict[str, Any]:
    """Scene variant — payload has NO `intake_envelope_id` key. The
    client-scene lineage is single-envelope. DISTINCT from
    `placement.emit_endorsement_quote_ready`."""
    return await _emit(
        store, tenant_id, type="EndorsementQuoteReady",
        actor={"kind": "system", "id": "orchestrator", "name": "orchestrator"},
        payload={
            "envelope_id": envelope_id,
            "policy_id": policy_id,
            "quote_id": quote_id,
        },
        idempotency_key=f"quote:ready:{envelope_id}",
    )


async def emit_payment_advised(
    store, *, tenant_id: str, envelope_id: str, policy_id: str,
    user_id: str, actor_role: str, amount: str, currency: str,
    method: str, paid_at: str,
) -> dict[str, Any]:
    """Client says they've paid. Never flips anything to Paid (M3-5)."""
    return await _emit(
        store, tenant_id, type=PAYMENT_ADVISED,
        actor={"kind": "human", "id": user_id, "name": actor_role},
        payload={
            "envelope_id": envelope_id,
            "policy_id": policy_id,
            "actor": actor_role,
            "amount": amount,
            "currency": currency,
            "method": method,
            "paid_at": paid_at,
            "mocked": True,
        },
        mocked=True,
        idempotency_key=f"payment:advised:{envelope_id}",
    )


async def emit_signature_requested(
    store, *, tenant_id: str, envelope_id: str, policy_id: str,
    req: dict[str, Any],
) -> dict[str, Any]:
    return await _emit(
        store, tenant_id, type="SignatureRequested",
        actor={"kind": "system", "id": "e_sign", "name": "e_sign"},
        payload={
            "envelope_id": envelope_id,
            "policy_id": policy_id,
            **req,
        },
        mocked=True,
        idempotency_key=f"esign:req:{envelope_id}",
    )


async def emit_signature_completed(
    store, *, tenant_id: str, envelope_id: str, policy_id: str,
    user_id: str, sig: dict[str, Any],
) -> dict[str, Any]:
    return await _emit(
        store, tenant_id, type="SignatureCompleted",
        actor={"kind": "human", "id": user_id, "name": "client_owner"},
        payload={
            "envelope_id": envelope_id,
            "policy_id": policy_id,
            **sig,
        },
        mocked=True,
        idempotency_key=f"esign:sig:{envelope_id}",
    )
