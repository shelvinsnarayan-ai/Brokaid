"""Client-side reference-scene endpoints (R5c-4b — Mongo-only).

Route surface unchanged:
  POST /api/c/matters/{policy_id}/request-change     intAIke → plAIce → carrier quote
  GET  /api/c/matters/{policy_id}/status             polling, returns latest state
  POST /api/c/matters/{policy_id}/pay/{envelope_id}  initiate + execute MOCKED payment
  POST /api/c/matters/{policy_id}/sign/{envelope_id} request + simulate MOCKED e-sign
  POST /api/c/matters/{policy_id}/finalise/{envelope_id}  endorsement + certificate

Every event append is delegated to `kernel.scene.emit_*`. Every
inline read is delegated to `kernel.scene`. brAIn / endorsement /
certificate carry their own kernel modules.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.sessions import Session
from app.kernel import scene
from app.kernel.agents.intake import INTAKE_ELIGIBLE_CLASSES
from app.kernel.agents.placement import PLACEMENT_ELIGIBLE_CLASSES
from app.kernel.brain import Brain
from app.kernel.certificate import regenerate_certificate_dispatch
from app.kernel.contracts.models import Actor, ContextEnvelope, InvocationBudget
from app.kernel.endorsement import execute_endorsement
from app.kernel.mocks import e_sign, payment_rail
from app.kernel.mocks.carrier_channel import submit_endorsement
from app.kernel.queue import enqueue as queue_enqueue
from app.middleware.session import require_session


router = APIRouter(prefix="/api/c/matters")


def _store(request: Request):
    s = getattr(request.app.state, "mongo_db", None)
    if s is None:
        raise HTTPException(status_code=503, detail="mongo_store_unavailable")
    return s


class RequestChangeBody(BaseModel):
    raw_message: str


@router.post("/{policy_id}/request-change")
async def request_change(
    policy_id: str,
    request: Request,
    body: RequestChangeBody = Body(...),
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    """Kick off the full scene: intAIke → plAIce → MOCKED quote → HITL enqueue."""
    store = _store(request)
    policy = await scene.load_policy(store, session.tenant_id, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="policy_not_found")

    tenant_id = session.tenant_id
    envelope_id = uuid.uuid4()

    await scene.emit_client_change_requested(
        store, tenant_id=tenant_id, user_id=session.user_id,
        envelope_id=str(envelope_id), policy_id=policy_id,
        client_id=policy["client_id"], raw_message=body.raw_message,
    )

    intake_envelope = ContextEnvelope(
        envelope_id=envelope_id,
        tenant_id=uuid.UUID(tenant_id),
        matter_id=None,
        client_id=uuid.UUID(policy["client_id"]),
        class_key="request_info",
        intent="parse client change request",
        inputs={
            "raw_message": body.raw_message,
            "policy_id": policy_id,
            "context_events_summary": "client-initiated change request",
        },
        context_events=[],
        risk_schema_refs=[],
        tenant_config={},
        budget=InvocationBudget(
            max_input_tokens=4000, max_output_tokens=1500, max_wall_ms=45000,
        ),
        caller=Actor(kind="human", id=session.user_id, name="client_owner"),
        ts=datetime.now(timezone.utc),
    )
    brain = Brain(store)
    intake_out = await brain.invoke(intake_envelope, "intake")
    intake_payload = intake_out.payload or {}
    proposed_class = intake_out.class_key or intake_payload.get("class_proposed") or ""

    if proposed_class not in PLACEMENT_ELIGIBLE_CLASSES:
        try:
            await queue_enqueue(
                store,
                tenant_id=tenant_id,
                envelope_id=str(envelope_id),
                class_key=(
                    proposed_class
                    if proposed_class in INTAKE_ELIGIBLE_CLASSES
                    else "request_info"
                ),
                proposal_ref=(
                    (await scene.last_event_id_for_envelope(
                        store, tenant_id, "AgentOutputProduced",
                        str(envelope_id),
                    )) or ""
                ),
                matter_id=None,
                client_id=policy["client_id"],
                gate_reason="intake_only",
                confidence=None,
                routed_to_role="account_executive",
            )
        except Exception:
            pass
        return {
            "state": "intake_only",
            "envelope_id": str(envelope_id),
            "class_proposed": proposed_class,
        }

    placement_envelope = ContextEnvelope(
        envelope_id=envelope_id,
        tenant_id=uuid.UUID(tenant_id),
        matter_id=None,
        client_id=uuid.UUID(policy["client_id"]),
        class_key=proposed_class,
        intent="draft submission for market",
        inputs={
            "intake_output": intake_payload,
            "policy_current": policy,
            "context_events_summary": "client-initiated change request",
        },
        context_events=[],
        risk_schema_refs=[],
        tenant_config={},
        budget=InvocationBudget(
            max_input_tokens=6000, max_output_tokens=2000, max_wall_ms=45000,
        ),
        caller=Actor(kind="human", id=session.user_id, name="client_owner"),
        ts=datetime.now(timezone.utc),
    )
    placement_out = await brain.invoke(placement_envelope, "placement")
    plc_payload = placement_out.payload or {}

    await scene.emit_submission_drafted(
        store, tenant_id=tenant_id,
        envelope_id=str(envelope_id), policy_id=policy_id,
        submission_summary=plc_payload.get("submission_summary"),
        structured_submission=plc_payload.get("structured_submission") or {},
        recommended_wording_ref=plc_payload.get("recommended_wording_ref"),
    )

    quote = await submit_endorsement(
        envelope_id=str(envelope_id),
        submission=plc_payload,
        policy_current=policy,
    )
    await scene.emit_carrier_quoted(
        store, tenant_id=tenant_id,
        envelope_id=str(envelope_id), policy_id=policy_id, quote=quote,
    )
    ready = await scene.emit_endorsement_quote_ready(
        store, tenant_id=tenant_id,
        envelope_id=str(envelope_id), policy_id=policy_id,
        quote_id=quote["quote_id"],
    )

    await queue_enqueue(
        store,
        tenant_id=tenant_id,
        envelope_id=str(envelope_id),
        class_key=proposed_class,
        proposal_ref=ready["event_id"],
        matter_id=None,
        client_id=policy["client_id"],
        gate_reason="quote_ready_operator_confirmation_required",
        confidence=0.9,
        routed_to_role="principal",
    )

    return {
        "state": "awaiting_operator",
        "envelope_id": str(envelope_id),
        "quote": quote,
        "submission_summary": plc_payload.get("submission_summary"),
    }


@router.get("/{policy_id}/status")
async def status(
    policy_id: str,
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    """Return the latest scene state for a client's policy."""
    store = _store(request)
    events = await scene.list_recent_events_for_policy(
        store, session.tenant_id, policy_id, limit=40,
    )
    return scene.compute_status(events)


@router.post("/{policy_id}/pay/{envelope_id}")
async def pay(
    policy_id: str,
    envelope_id: str,
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    """MOCKED PayTo/PayNow: mandate + execute + emit trio."""
    store = _store(request)
    tenant_id = session.tenant_id
    policy = await scene.load_policy(store, tenant_id, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="policy_not_found")

    quote = await scene.find_carrier_quote(store, tenant_id, envelope_id)
    if not quote:
        raise HTTPException(status_code=404, detail="quote_not_found")
    jurisdiction = await scene.find_client_jurisdiction(
        store, tenant_id, policy["client_id"],
    )
    rail_label = "PayNow" if jurisdiction == "SG" else "PayTo"
    amount = quote.get("premium_change_amount") or "0"
    currency = quote.get("premium_currency") or policy["premium_currency"]

    mandate = await payment_rail.initiate_mandate(
        envelope_id=envelope_id, client_id=policy["client_id"],
        amount=amount, currency=currency, rail_label=rail_label,
    )
    await scene.emit_payment_mandate_created(
        store, tenant_id=tenant_id,
        envelope_id=envelope_id, policy_id=policy_id,
        user_id=session.user_id, mandate=mandate,
    )
    await scene.emit_payment_initiated(
        store, tenant_id=tenant_id,
        envelope_id=envelope_id, policy_id=policy_id,
        mandate_id=mandate["mandate_id"], amount=amount,
        currency=currency, rail_label=rail_label,
    )
    receipt = await payment_rail.execute_payment(
        envelope_id=envelope_id, mandate_id=mandate["mandate_id"],
        quote_id=quote["quote_id"], amount=amount, currency=currency,
        rail_label=rail_label,
    )
    await scene.emit_payment_received(
        store, tenant_id=tenant_id,
        envelope_id=envelope_id, policy_id=policy_id, receipt=receipt,
    )
    return {"state": "awaiting_signature", "payment": receipt}


@router.post("/{policy_id}/sign/{envelope_id}")
async def sign(
    policy_id: str,
    envelope_id: str,
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    store = _store(request)
    tenant_id = session.tenant_id
    signer_email = f"{session.user_id}@example.test"
    req = await e_sign.request_signature(
        envelope_id=envelope_id,
        document_ref=f"endorsement:{envelope_id}",
        signer_email=signer_email,
    )
    await scene.emit_signature_requested(
        store, tenant_id=tenant_id,
        envelope_id=envelope_id, policy_id=policy_id, req=req,
    )
    sig = await e_sign.simulate_signature(
        envelope_id=envelope_id, request_id=req["request_id"],
    )
    await scene.emit_signature_completed(
        store, tenant_id=tenant_id,
        envelope_id=envelope_id, policy_id=policy_id,
        user_id=session.user_id, sig=sig,
    )
    return {"state": "executing", "signature": sig}


@router.post("/{policy_id}/finalise/{envelope_id}")
async def finalise(
    policy_id: str,
    envelope_id: str,
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    """Run endorsement execution + certificate regeneration."""
    store = _store(request)
    tenant_id = session.tenant_id
    result = await execute_endorsement(
        store=store, tenant_id=tenant_id,
        envelope_id=envelope_id, policy_id=policy_id,
        approver_actor={"kind": "system", "id": "endorsement", "name": "endorsement"},
    )
    cert = await regenerate_certificate_dispatch(
        store=store, tenant_id=tenant_id, policy_id=policy_id,
        envelope_id=envelope_id,
    )
    return {
        "state": "certificate_ready",
        "endorsement": result,
        "certificate": cert["certificate"],
        "verifier_token": cert["raw_token"],
        "expires_at": cert["expires_at"],
    }
