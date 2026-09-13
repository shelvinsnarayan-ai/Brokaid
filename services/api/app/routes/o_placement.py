"""Operator placement route — orchestrates plAIce over an intake output.

POST /api/o/matters/{matter_id}/place
  Body: { "intake_envelope_id": UUID, "policy_id": UUID }

Loads the intake OutputContract from the ledger, builds a placement
envelope, invokes brAIn on it, then submits to the MOCKED CarrierChannel
and lands three synthesis events on the ledger.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.sessions import Session
from app.kernel.agents.placement import (
    AgentClassMismatch,
    EmergentLLMProviderPlacement,
    PLACEMENT_ELIGIBLE_CLASSES,
)
from app.kernel.brain import Brain
from app.kernel.contracts.models import (
    Actor,
    ContextEnvelope,
    InvocationBudget,
)
from app.kernel.mocks.carrier_channel import submit_endorsement
from app.kernel.trail import append
from app.middleware.session import require_operator_session


router = APIRouter(prefix="/api/o/matters")


class PlaceBody(BaseModel):
    intake_envelope_id: str
    policy_id: str


def _parse(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try: return json.loads(raw)
        except Exception: return {}
    return {}


async def _load_intake_output(pool, tenant_id: str, envelope_id: str) -> dict | None:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            row = await c.fetchrow(
                "SELECT payload FROM events "
                " WHERE tenant_id=$1::uuid AND type='AgentOutputProduced' "
                "   AND payload->>'envelope_id' = $2 "
                "   AND payload->>'agent_name' = 'intake' "
                " ORDER BY seq DESC LIMIT 1",
                tenant_id, envelope_id,
            )
    return _parse(row["payload"]) if row else None


async def _load_policy(pool, tenant_id: str, policy_id: str) -> dict | None:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
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


@router.post("/{matter_id}/place")
async def place(
    matter_id: str,
    request: Request,
    body: PlaceBody = Body(...),
    session: Session = Depends(require_operator_session),
) -> dict[str, Any]:
    role = session.active_role
    if role not in ("account_executive", "ae", "principal"):
        raise HTTPException(status_code=403, detail="ae_or_principal_only")

    pool = request.app.state.pool

    # 1. Load intake output. The intake agent's payload contains
    #    parsed_intent + proposed_changes + class_proposed.
    intake_payload = await _load_intake_output(pool, session.tenant_id, body.intake_envelope_id)
    if not intake_payload:
        raise HTTPException(status_code=404, detail="intake_output_not_found")

    intake_output_obj = _parse(intake_payload)
    proposed_class = intake_output_obj.get("class_key") or "submit_to_market"
    if proposed_class not in PLACEMENT_ELIGIBLE_CLASSES:
        # intake routed elsewhere (e.g. comms_advisory H) — refuse to place.
        raise HTTPException(status_code=409, detail={
            "reason": "class_not_placeable",
            "intake_class": proposed_class,
        })

    # 2. Load current policy schedule for the placement envelope.
    policy_current = await _load_policy(pool, session.tenant_id, body.policy_id)
    if not policy_current:
        raise HTTPException(status_code=404, detail="policy_not_found")

    # 3. Build the placement envelope.
    placement_envelope_id = uuid.uuid4()
    envelope = ContextEnvelope(
        envelope_id=placement_envelope_id,
        tenant_id=uuid.UUID(session.tenant_id),
        matter_id=uuid.UUID(matter_id),
        client_id=uuid.UUID(policy_current["client_id"]),
        class_key=proposed_class,
        intent="draft submission for market",
        inputs={
            "intake_output": intake_output_obj,
            "policy_current": policy_current,
            "context_events_summary": "placement invoked by operator",
        },
        context_events=[],
        risk_schema_refs=[],
        tenant_config={},
        budget=InvocationBudget(
            max_input_tokens=6000,
            max_output_tokens=2000,
            max_wall_ms=45000,
        ),
        caller=Actor(kind="human", id=session.user_id, name=role),
        ts=datetime.now(timezone.utc),
    )

    # 4. Run plAIce via brAIn.
    brain = Brain(pool)
    try:
        placement_output = await brain.invoke(envelope, "placement")
    except AgentClassMismatch as e:
        raise HTTPException(status_code=409, detail=f"placement_refused:{e!s}")

    plc_payload = placement_output.payload or {}
    reroute = plc_payload.get("class_reroute")
    if reroute:
        # plAIce refused (coverage_position or new-cover-bind). Emit a
        # synthesis event for HITL routing and stop — no carrier call.
        await append(
            pool, session.tenant_id, "PlacementRerouted",
            actor={"kind": "system", "id": "placement", "name": "placement"},
            payload={
                "envelope_id": str(placement_envelope_id),
                "reroute_class": reroute,
                "intake_envelope_id": body.intake_envelope_id,
                "policy_id": body.policy_id,
                "notes": plc_payload.get("notes") or "",
            },
            idempotency_key=f"placement:reroute:{placement_envelope_id}",
        )
        return {
            "state": "rerouted",
            "envelope_id": str(placement_envelope_id),
            "reroute_class": reroute,
            "notes": plc_payload.get("notes") or "",
        }

    # 5. SubmissionDrafted (plAIce's output).
    await append(
        pool, session.tenant_id, "SubmissionDrafted",
        actor={"kind": "system", "id": "placement", "name": "placement"},
        payload={
            "envelope_id": str(placement_envelope_id),
            "submission_summary": plc_payload.get("submission_summary"),
            "structured_submission": plc_payload.get("structured_submission") or {},
            "recommended_wording_ref": plc_payload.get("recommended_wording_ref"),
            "policy_id": body.policy_id,
            "mocked": False,
        },
        idempotency_key=f"placement:submission:{placement_envelope_id}",
    )

    # 6. Call the MOCKED CarrierChannel.
    quote = await submit_endorsement(
        envelope_id=str(placement_envelope_id),
        submission=plc_payload,
        policy_current=policy_current,
    )

    # 7. CarrierQuoted (MOCKED).
    await append(
        pool, session.tenant_id, "CarrierQuoted",
        actor={"kind": "system", "id": "carrier_channel", "name": "carrier"},
        payload={
            "envelope_id": str(placement_envelope_id),
            "policy_id": body.policy_id,
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
        idempotency_key=f"carrier:quote:{placement_envelope_id}",
    )

    # 8. EndorsementQuoteReady (synthesis).
    await append(
        pool, session.tenant_id, "EndorsementQuoteReady",
        actor={"kind": "system", "id": "orchestrator", "name": "orchestrator"},
        payload={
            "envelope_id": str(placement_envelope_id),
            "policy_id": body.policy_id,
            "quote_id": quote["quote_id"],
            "intake_envelope_id": body.intake_envelope_id,
        },
        idempotency_key=f"quote:ready:{placement_envelope_id}",
    )

    return {
        "state": "ready",
        "envelope_id": str(placement_envelope_id),
        "quote": quote,
        "submission_summary": plc_payload.get("submission_summary"),
    }
