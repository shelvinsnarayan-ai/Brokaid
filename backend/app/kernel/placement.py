"""Phase R5a-2b-α — placement kernel module (dispatch-aware).

Extract-first discipline: this module is a verbatim move of the four
`trail.append` sites and two Postgres readers from `o_placement.py`.
No behavioural change — same event types, same payload keys, same
idempotency_key patterns, same actor shapes. The only difference is
the top-level function boundary and the polymorphic `store` param.

Under Postgres store: reads go through inline SQL (same as pre-
extraction); writes go through `trail_dispatch.append_via_dispatch`
which delegates to `trail.append` — byte-identical event hash.

Under Mongo store: reads go through `TenantSafeDatabase` under
`use_tenant(tid)`; writes go through `trail_dispatch` → Mongo
`append_event` — same JCS canonicalisation, same hash algo, byte-
identical event hash for the same input.

Byte-identical ledger parity (root's R5a-2b acceptance gate) holds
by construction: same tenant_id + same actor + same type + same
JCS-canonicalised payload + same prev_hash ⇒ same event_hash.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.tenant_safe import TenantSafeDatabase, wrap
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch


def _is_mongo(store) -> bool:
    return isinstance(store, (AsyncIOMotorDatabase, TenantSafeDatabase))


def _parse(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


# ---- Read side --------------------------------------------------------
async def load_intake_output(
    store, tenant_id: str, envelope_id: str,
) -> Optional[dict]:
    """Load the intake AgentOutputProduced payload for an envelope."""
    if _is_mongo(store):
        safe_db = wrap(store)
        async with use_tenant(tenant_id):
            async for r in safe_db.events.find({
                "type": "AgentOutputProduced",
            }).sort("seq", -1):
                p = r.get("payload") or {}
                if (
                    p.get("envelope_id") == envelope_id
                    and p.get("agent_name") == "intake"
                ):
                    return _parse(p)
        return None
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            row = await c.fetchrow(
                "SELECT payload FROM events "
                " WHERE tenant_id=$1::uuid AND type='AgentOutputProduced' "
                "   AND payload->>'envelope_id' = $2 "
                "   AND payload->>'agent_name' = 'intake' "
                " ORDER BY seq DESC LIMIT 1",
                tenant_id, envelope_id,
            )
    return _parse(row["payload"]) if row else None


async def load_policy(store, tenant_id: str, policy_id: str) -> Optional[dict]:
    if _is_mongo(store):
        safe_db = wrap(store)
        async with use_tenant(tenant_id):
            row = await safe_db.policies.find_one({"policy_id": policy_id})
        if not row:
            return None
        return {
            "policy_id": row["policy_id"],
            "client_id": row["client_id"],
            "product": row.get("product"),
            "schedule": _parse(row.get("schedule") or {}),
            "premium_amount": str(row.get("premium_amount") or "0"),
            "premium_currency": row.get("premium_currency"),
            "status": row.get("status"),
        }
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            row = await c.fetchrow(
                "SELECT policy_id::text, client_id::text, product, schedule, "
                "       premium_amount::text, premium_currency, status "
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
        "schedule": _parse(row["schedule"]),
        "premium_amount": row["premium_amount"],
        "premium_currency": row["premium_currency"],
        "status": row["status"],
    }


# ---- Write side (4 event emitters, extract-first verbatim) -----------
async def _emit(store, tenant_id: str, **kw):
    if _is_mongo(store):
        async with use_tenant(tenant_id):
            return await append_via_dispatch(store, tenant_id, **kw)
    return await append_via_dispatch(store, tenant_id, **kw)


async def emit_placement_rerouted(
    store, *, tenant_id: str, envelope_id: str,
    reroute_class: str, intake_envelope_id: str,
    policy_id: str, notes: str,
) -> dict:
    return await _emit(
        store, tenant_id, type="PlacementRerouted",
        actor={"kind": "system", "id": "placement", "name": "placement"},
        payload={
            "envelope_id": envelope_id,
            "reroute_class": reroute_class,
            "intake_envelope_id": intake_envelope_id,
            "policy_id": policy_id,
            "notes": notes,
        },
        idempotency_key=f"placement:reroute:{envelope_id}",
    )


async def emit_submission_drafted(
    store, *, tenant_id: str, envelope_id: str,
    submission_summary: str | None,
    structured_submission: dict,
    recommended_wording_ref: str | None,
    policy_id: str,
) -> dict:
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
    store, *, tenant_id: str, envelope_id: str, policy_id: str, quote: dict,
) -> dict:
    return await _emit(
        store, tenant_id, type="CarrierQuoted",
        actor={"kind": "system", "id": "carrier_channel", "name": "carrier"},
        payload={
            "envelope_id": envelope_id,
            "policy_id": policy_id,
            "quote_id": quote["quote_id"],
            "carrier_name": quote["carrier_name"],
            "premium_change_amount": quote["premium_change_amount"],
            "premium_new_annual": quote["premium_new_annual"],
            "premium_currency": quote["premium_currency"],
            "terms": quote["terms"],
            "conditions": quote["conditions"],
            "quoted_at": quote["quoted_at"],
            "mocked": True,
        },
        mocked=True,
        idempotency_key=f"carrier:quote:{envelope_id}",
    )


async def emit_endorsement_quote_ready(
    store, *, tenant_id: str, envelope_id: str, policy_id: str,
    quote_id: str, intake_envelope_id: str,
) -> dict:
    return await _emit(
        store, tenant_id, type="EndorsementQuoteReady",
        actor={"kind": "system", "id": "orchestrator", "name": "orchestrator"},
        payload={
            "envelope_id": envelope_id,
            "policy_id": policy_id,
            "quote_id": quote_id,
            "intake_envelope_id": intake_envelope_id,
        },
        idempotency_key=f"quote:ready:{envelope_id}",
    )
