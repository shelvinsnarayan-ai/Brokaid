"""Client-side reference-scene endpoints.

Flow:
  POST /api/c/matters/{policy_id}/request-change     - intAIke → plAIce → carrier quote
  GET  /api/c/matters/{policy_id}/status             - polling, returns latest state
  POST /api/c/matters/{policy_id}/pay/{envelope_id}  - initiate + execute MOCKED payment
  POST /api/c/matters/{policy_id}/sign/{envelope_id} - request + simulate MOCKED e-sign
  POST /api/c/matters/{policy_id}/finalise/{envelope_id} - if approval + payment + sign
                                                        present, regenerate certificate
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.sessions import Session
from app.kernel.agents.intake import EmergentLLMProviderIntake, INTAKE_ELIGIBLE_CLASSES
from app.kernel.agents.placement import (
    EmergentLLMProviderPlacement,
    PLACEMENT_ELIGIBLE_CLASSES,
)
from app.kernel.brain import Brain
from app.kernel.certificate import regenerate_certificate
from app.kernel.contracts.models import Actor, ContextEnvelope, InvocationBudget
from app.kernel.endorsement import execute_endorsement
from app.kernel.mocks import e_sign, payment_rail
from app.kernel.mocks.carrier_channel import submit_endorsement
from app.kernel.queue import enqueue as queue_enqueue
from app.kernel.trail import append
from app.middleware.session import require_session


router = APIRouter(prefix="/api/c/matters")


class RequestChangeBody(BaseModel):
    raw_message: str


def _parse(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        try: return json.loads(v)
        except Exception: return {}
    return {}


async def _load_policy(pool, tenant_id: str, policy_id: str) -> dict | None:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            row = await c.fetchrow(
                "SELECT policy_id::text, client_id::text, product, schedule, "
                "       premium_amount::text, premium_currency, insurer_display, "
                "       inception, expiry, status "
                "  FROM policies "
                " WHERE tenant_id=$1::uuid AND policy_id=$2::uuid",
                tenant_id, policy_id,
            )
    if not row: return None
    return {
        "policy_id": row["policy_id"], "client_id": row["client_id"],
        "product": row["product"], "schedule": _parse(row["schedule"]),
        "premium_amount": row["premium_amount"],
        "premium_currency": row["premium_currency"],
        "insurer_display": row["insurer_display"],
        "status": row["status"],
    }


@router.post("/{policy_id}/request-change")
async def request_change(
    policy_id: str,
    request: Request,
    body: RequestChangeBody = Body(...),
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    """Kick off the full scene: intAIke → plAIce → MOCKED quote → HITL enqueue."""
    pool = request.app.state.pool
    policy = await _load_policy(pool, session.tenant_id, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="policy_not_found")

    tenant_id = session.tenant_id
    envelope_id = uuid.uuid4()

    # 1. ClientChangeRequested — client-actor event.
    await append(
        pool, tenant_id, "ClientChangeRequested",
        actor={"kind": "human", "id": session.user_id, "name": "client_owner"},
        payload={
            "envelope_id": str(envelope_id),
            "policy_id": policy_id,
            "client_id": policy["client_id"],
            "raw_message": body.raw_message,
        },
        idempotency_key=f"cchreq:{envelope_id}",
    )

    # 2. intAIke envelope.
    intake_envelope = ContextEnvelope(
        envelope_id=envelope_id,
        tenant_id=uuid.UUID(tenant_id),
        matter_id=None,
        client_id=uuid.UUID(policy["client_id"]),
        class_key="request_info",           # provisional; intake proposes.
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
    brain = Brain(pool)
    intake_out = await brain.invoke(intake_envelope, "intake")
    intake_payload = intake_out.payload or {}
    proposed_class = intake_out.class_key or intake_payload.get("class_proposed") or ""

    # If class not placement-eligible, still emit an HITL queue card so
    # the operator picks it up.
    if proposed_class not in PLACEMENT_ELIGIBLE_CLASSES:
        # Enqueue as-is on the class intake proposed.
        try:
            await queue_enqueue(
                pool,
                tenant_id=tenant_id,
                envelope_id=str(envelope_id),
                class_key=(
                    proposed_class
                    if proposed_class in INTAKE_ELIGIBLE_CLASSES
                    else "request_info"
                ),
                proposal_ref=(
                    (await _last_event_id(pool, tenant_id, "AgentOutputProduced",
                                          str(envelope_id))) or ""
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

    # 3. plAIce envelope — SAME envelope_id so the whole scene shares one lineage.
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

    # 4. SubmissionDrafted.
    await append(
        pool, tenant_id, "SubmissionDrafted",
        actor={"kind": "system", "id": "placement", "name": "placement"},
        payload={
            "envelope_id": str(envelope_id),
            "submission_summary": plc_payload.get("submission_summary"),
            "structured_submission": plc_payload.get("structured_submission") or {},
            "recommended_wording_ref": plc_payload.get("recommended_wording_ref"),
            "policy_id": policy_id,
            "mocked": False,
        },
        idempotency_key=f"placement:submission:{envelope_id}",
    )

    # 5. MOCKED carrier quote.
    quote = await submit_endorsement(
        envelope_id=str(envelope_id),
        submission=plc_payload,
        policy_current=policy,
    )
    await append(
        pool, tenant_id, "CarrierQuoted",
        actor={"kind": "system", "id": "carrier_channel", "name": "carrier"},
        payload={
            "envelope_id": str(envelope_id),
            "policy_id": policy_id,
            **quote,
        },
        mocked=True,
        idempotency_key=f"carrier:quote:{envelope_id}",
    )
    ready = await append(
        pool, tenant_id, "EndorsementQuoteReady",
        actor={"kind": "system", "id": "orchestrator", "name": "orchestrator"},
        payload={
            "envelope_id": str(envelope_id),
            "policy_id": policy_id,
            "quote_id": quote["quote_id"],
        },
        idempotency_key=f"quote:ready:{envelope_id}",
    )

    # 6. Enqueue HITL card (phase 11: always HITL — class not unlocked).
    await queue_enqueue(
        pool,
        tenant_id=tenant_id,
        envelope_id=str(envelope_id),
        class_key=proposed_class,
        proposal_ref=ready.event_id,
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


async def _last_event_id(
    pool, tenant_id: str, type_: str, envelope_id: str,
) -> str | None:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            row = await c.fetchrow(
                "SELECT event_id::text FROM events "
                " WHERE tenant_id=$1::uuid AND type=$2 "
                "   AND payload->>'envelope_id' = $3 "
                " ORDER BY seq DESC LIMIT 1",
                tenant_id, type_, envelope_id,
            )
    return row["event_id"] if row else None


@router.get("/{policy_id}/status")
async def status(
    policy_id: str,
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    """Return the latest scene state for a client's policy.

    States: 'idle' | 'intake_running' | 'placement_running' | 'awaiting_operator'
            | 'awaiting_payment' | 'awaiting_signature' | 'executing'
            | 'certificate_ready'
    """
    pool = request.app.state.pool
    tenant_id = session.tenant_id
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            rows = await c.fetch(
                """
                SELECT type, payload FROM events
                 WHERE tenant_id=$1::uuid
                   AND payload->>'policy_id' = $2
                 ORDER BY seq DESC
                 LIMIT 40
                """,
                tenant_id, policy_id,
            )
    latest_types: list[str] = []
    payloads: dict[str, dict] = {}
    envelope_id: str | None = None
    for r in rows:
        p = _parse(r["payload"])
        if envelope_id is None:
            envelope_id = p.get("envelope_id")
        payloads.setdefault(r["type"], p)
        latest_types.append(r["type"])
    state = "idle"
    if "CertificateRegenerated" in latest_types:
        state = "certificate_ready"
    elif "EndorsementExecuted" in latest_types:
        state = "executing"
    elif "SignatureCompleted" in latest_types:
        state = "executing"
    elif "SignatureRequested" in latest_types:
        state = "awaiting_signature"
    elif "PaymentReceived" in latest_types:
        state = "awaiting_signature"
    elif "Approval" in latest_types:
        state = "awaiting_payment"
    elif "EndorsementQuoteReady" in latest_types:
        state = "awaiting_operator"
    elif "SubmissionDrafted" in latest_types:
        state = "placement_running"
    elif "ClientChangeRequested" in latest_types:
        state = "intake_running"
    return {
        "state": state,
        "envelope_id": envelope_id,
        "recent_types": latest_types[:10],
        "quote_id": (payloads.get("CarrierQuoted") or {}).get("quote_id"),
        "certificate": (payloads.get("CertificateRegenerated") or {}).get("certificate"),
        "verifier_token_hash": (
            (payloads.get("VerifierTokenIssued") or {}).get("token_hash")
        ),
    }


@router.post("/{policy_id}/pay/{envelope_id}")
async def pay(
    policy_id: str,
    envelope_id: str,
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    """MOCKED PayTo/PayNow: mandate + execute + emit trio."""
    pool = request.app.state.pool
    tenant_id = session.tenant_id
    policy = await _load_policy(pool, tenant_id, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="policy_not_found")

    # Look up the CarrierQuoted for pricing.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            q_row = await c.fetchrow(
                "SELECT payload FROM events "
                " WHERE tenant_id=$1::uuid AND type='CarrierQuoted' "
                "   AND payload->>'envelope_id' = $2 "
                " ORDER BY seq DESC LIMIT 1",
                tenant_id, envelope_id,
            )
            # Jurisdiction to pick rail label.
            j_row = await c.fetchrow(
                "SELECT jurisdiction_code FROM clients "
                "WHERE tenant_id=$1::uuid AND client_id=$2::uuid",
                tenant_id, policy["client_id"],
            )
    if not q_row:
        raise HTTPException(status_code=404, detail="quote_not_found")
    quote = _parse(q_row["payload"])
    rail_label = "PayNow" if (j_row and j_row["jurisdiction_code"] == "SG") else "PayTo"
    amount = quote.get("premium_change_amount") or "0"
    currency = quote.get("premium_currency") or policy["premium_currency"]

    mandate = await payment_rail.initiate_mandate(
        envelope_id=envelope_id, client_id=policy["client_id"],
        amount=amount, currency=currency, rail_label=rail_label,
    )
    await append(
        pool, tenant_id, "PaymentMandateCreated",
        actor={"kind": "human", "id": session.user_id, "name": "client_owner"},
        payload={"envelope_id": envelope_id, "policy_id": policy_id, **mandate},
        mocked=True,
        idempotency_key=f"payment:mandate:{envelope_id}",
    )
    await append(
        pool, tenant_id, "PaymentInitiated",
        actor={"kind": "system", "id": "payment_rail", "name": "payment"},
        payload={
            "envelope_id": envelope_id, "policy_id": policy_id,
            "mandate_id": mandate["mandate_id"], "amount": amount,
            "currency": currency, "rail_label": rail_label, "mocked": True,
        },
        mocked=True,
        idempotency_key=f"payment:initiated:{envelope_id}",
    )
    receipt = await payment_rail.execute_payment(
        envelope_id=envelope_id, mandate_id=mandate["mandate_id"],
        quote_id=quote["quote_id"], amount=amount, currency=currency,
        rail_label=rail_label,
    )
    await append(
        pool, tenant_id, "PaymentReceived",
        actor={"kind": "system", "id": "payment_rail", "name": "payment"},
        payload={"envelope_id": envelope_id, "policy_id": policy_id, **receipt},
        mocked=True,
        idempotency_key=f"payment:received:{envelope_id}",
    )
    return {"state": "awaiting_signature", "payment": receipt}


@router.post("/{policy_id}/sign/{envelope_id}")
async def sign(
    policy_id: str,
    envelope_id: str,
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    pool = request.app.state.pool
    tenant_id = session.tenant_id
    signer_email = f"{session.user_id}@example.test"
    req = await e_sign.request_signature(
        envelope_id=envelope_id,
        document_ref=f"endorsement:{envelope_id}",
        signer_email=signer_email,
    )
    await append(
        pool, tenant_id, "SignatureRequested",
        actor={"kind": "system", "id": "e_sign", "name": "e_sign"},
        payload={"envelope_id": envelope_id, "policy_id": policy_id, **req},
        mocked=True,
        idempotency_key=f"esign:req:{envelope_id}",
    )
    sig = await e_sign.simulate_signature(
        envelope_id=envelope_id, request_id=req["request_id"],
    )
    await append(
        pool, tenant_id, "SignatureCompleted",
        actor={"kind": "human", "id": session.user_id, "name": "client_owner"},
        payload={"envelope_id": envelope_id, "policy_id": policy_id, **sig},
        mocked=True,
        idempotency_key=f"esign:sig:{envelope_id}",
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
    pool = request.app.state.pool
    tenant_id = session.tenant_id
    result = await execute_endorsement(
        pool=pool, tenant_id=tenant_id,
        envelope_id=envelope_id, policy_id=policy_id,
        approver_actor={"kind": "system", "id": "endorsement", "name": "endorsement"},
    )
    cert = await regenerate_certificate(
        pool=pool, tenant_id=tenant_id, policy_id=policy_id,
        envelope_id=envelope_id,
    )
    return {
        "state": "certificate_ready",
        "endorsement": result,
        "certificate": cert["certificate"],
        "verifier_token": cert["raw_token"],
        "expires_at": cert["expires_at"],
    }
