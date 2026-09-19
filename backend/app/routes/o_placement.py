"""Operator placement route — orchestrates plAIce over an intake output (R5c-4b — Mongo-only)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.sessions import Session
from app.kernel import placement as placement_kernel
from app.kernel.agents.placement import (
    AgentClassMismatch,
    PLACEMENT_ELIGIBLE_CLASSES,
)
from app.kernel.brain import Brain
from app.kernel.contracts.models import (
    Actor,
    ContextEnvelope,
    InvocationBudget,
)
from app.kernel.mocks.carrier_channel import submit_endorsement
from app.middleware.session import require_operator_session


router = APIRouter(prefix="/api/o/matters")


class PlaceBody(BaseModel):
    intake_envelope_id: str
    policy_id: str


def _store(request: Request):
    s = getattr(request.app.state, "mongo_db", None)
    if s is None:
        raise HTTPException(status_code=503, detail="mongo_store_unavailable")
    return s


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

    store = _store(request)

    intake_payload = await placement_kernel.load_intake_output(
        store, session.tenant_id, body.intake_envelope_id,
    )
    if not intake_payload:
        raise HTTPException(status_code=404, detail="intake_output_not_found")

    intake_output_obj = intake_payload if isinstance(intake_payload, dict) else {}
    proposed_class = intake_output_obj.get("class_key") or "submit_to_market"
    if proposed_class not in PLACEMENT_ELIGIBLE_CLASSES:
        raise HTTPException(status_code=409, detail={
            "reason": "class_not_placeable",
            "intake_class": proposed_class,
        })

    policy_current = await placement_kernel.load_policy(
        store, session.tenant_id, body.policy_id,
    )
    if not policy_current:
        raise HTTPException(status_code=404, detail="policy_not_found")

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

    brain = Brain(store)
    try:
        placement_output = await brain.invoke(envelope, "placement")
    except AgentClassMismatch as e:
        raise HTTPException(status_code=409, detail=f"placement_refused:{e!s}")

    plc_payload = placement_output.payload or {}
    reroute = plc_payload.get("class_reroute")
    if reroute:
        await placement_kernel.emit_placement_rerouted(
            store, tenant_id=session.tenant_id,
            envelope_id=str(placement_envelope_id),
            reroute_class=reroute,
            intake_envelope_id=body.intake_envelope_id,
            policy_id=body.policy_id,
            notes=plc_payload.get("notes") or "",
        )
        return {
            "state": "rerouted",
            "envelope_id": str(placement_envelope_id),
            "reroute_class": reroute,
            "notes": plc_payload.get("notes") or "",
        }

    await placement_kernel.emit_submission_drafted(
        store, tenant_id=session.tenant_id,
        envelope_id=str(placement_envelope_id),
        submission_summary=plc_payload.get("submission_summary"),
        structured_submission=plc_payload.get("structured_submission") or {},
        recommended_wording_ref=plc_payload.get("recommended_wording_ref"),
        policy_id=body.policy_id,
    )

    quote = await submit_endorsement(
        envelope_id=str(placement_envelope_id),
        submission=plc_payload,
        policy_current=policy_current,
    )

    await placement_kernel.emit_carrier_quoted(
        store, tenant_id=session.tenant_id,
        envelope_id=str(placement_envelope_id),
        policy_id=body.policy_id, quote=quote,
    )

    await placement_kernel.emit_endorsement_quote_ready(
        store, tenant_id=session.tenant_id,
        envelope_id=str(placement_envelope_id),
        policy_id=body.policy_id,
        quote_id=quote["quote_id"],
        intake_envelope_id=body.intake_envelope_id,
    )

    return {
        "state": "ready",
        "envelope_id": str(placement_envelope_id),
        "quote": quote,
        "submission_summary": plc_payload.get("submission_summary"),
    }
