"""Client-side reference-scene endpoints (R5c-4b — Mongo-only).

Route surface unchanged:
  POST /api/c/matters/{policy_id}/request-change     intAIke → plAIce → carrier quote
  GET  /api/c/matters/{policy_id}/status             polling, returns latest state
  GET|POST /api/c/matters/{policy_id}/pay/{envelope_id}  MOCKED BPAY/EFT payment details
  POST /api/c/matters/{policy_id}/paid/{envelope_id} client advises payment (payment.advised)
  POST /api/c/matters/{policy_id}/sign/{envelope_id} request + simulate MOCKED e-sign
  POST /api/c/matters/{policy_id}/finalise/{envelope_id}  endorsement + certificate

Every event append is delegated to `kernel.scene.emit_*`. Every
inline read is delegated to `kernel.scene`. brAIn / endorsement /
certificate carry their own kernel modules.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.sessions import Session
from app.kernel import scene
from app.kernel.agents.intake import INTAKE_ELIGIBLE_CLASSES
from app.kernel.agents.placement import PLACEMENT_ELIGIBLE_CLASSES
from app.kernel.brain import Brain
from app.kernel.certificate import regenerate_certificate_dispatch
from app.kernel.contracts.models import Actor, ContextEnvelope, InvocationBudget
from app.kernel.endorsement import execute_endorsement
from app.kernel.mocks import e_sign
from app.kernel.mocks.carrier_channel import submit_endorsement
from app.kernel.advice_boundary import classify
from app.kernel.authority import require_authority
from app.kernel import scene_mongo
from app.kernel.policy_field_guard import UnmatchedPolicyField, normalize_vehicle_ops, validate_requested_changes
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.kernel.trail_mongo import DuplicateIdempotencyKey
from app.kernel.workflow import enqueue as queue_enqueue
from app.middleware.session import require_session


router = APIRouter(prefix="/api/c/matters")


class LineageIncomplete(HTTPException):
    """Structured 409 for the failsafe surface (R1 #2 tester follow-up).

    The internal `EndorsementLineageIncomplete` raise from
    `kernel.endorsement.execute_endorsement` is wrapped here + the
    /approve, /pay, /sign endpoints emit an early guard so operators
    see a structured code before the internal exception fires.

    Body: {"code": "EndorsementLineageIncomplete",
           "detail": "<human-readable missing predecessor(s)>"}
    """

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=409,
            detail={
                "code": "EndorsementLineageIncomplete",
                "detail": detail,
            },
        )


async def _find_envelope_event(store, tenant_id: str, envelope_id: str,
                               type_: str) -> dict | None:
    """Latest event of `type_` scoped to a single envelope, or None.

    Uses TenantSafeDatabase under `use_tenant(tenant_id)` so tenant
    isolation is preserved.
    """
    from app.db.tenant_safe import wrap
    safe = wrap(store)
    async with use_tenant(tenant_id):
        return await safe.events.find_one(
            {"type": type_, "payload.envelope_id": envelope_id},
            sort=[("seq", -1)],
        )


async def _require_predecessor(store, tenant_id: str, envelope_id: str,
                               required: list[str]) -> None:
    """Raise 409 LineageIncomplete listing every missing predecessor."""
    missing: list[str] = []
    for t in required:
        row = await _find_envelope_event(store, tenant_id, envelope_id, t)
        if row is None:
            missing.append(t)
    if missing:
        raise LineageIncomplete(
            f"missing upstream event(s) for envelope {envelope_id}: "
            f"{', '.join(missing)}"
        )


async def _require_client_approval(store, tenant_id: str, envelope_id: str) -> None:
    """R0.12 item 0: payment needs the CLIENT's own Approval (it carries policy_id). The broker's
    queue-side `Approval` (no policy_id, lineage for execute_endorsement) never counts."""
    from app.db.tenant_safe import wrap
    async with use_tenant(tenant_id):
        row = await wrap(store).events.find_one({"type": "Approval", "payload.envelope_id": envelope_id,
                                                 "payload.policy_id": {"$exists": True}})
    if row is None:
        raise LineageIncomplete(f"missing upstream event(s) for envelope {envelope_id}: client Approval")


def _store(request: Request):
    s = getattr(request.app.state, "mongo_db", None)
    if s is None:
        raise HTTPException(status_code=503, detail="mongo_store_unavailable")
    return s


class RequestChangeBody(BaseModel):
    raw_message: str
    idempotency_key: str | None = Field(default=None, max_length=100)


# Background-task handles kept alive here so asyncio.create_task doesn't
# garbage-collect them before the chain finishes.
_PENDING_TASKS: set[asyncio.Task] = set()


async def _find_existing_envelope_by_idempotency(
    store, tenant_id: str, policy_id: str, user_id: str, idem: str,
) -> str | None:
    from app.db.tenant_safe import wrap
    safe = wrap(store)
    async with use_tenant(tenant_id):
        row = await safe.events.find_one({
            "type": "ClientChangeRequested",
            "payload.policy_id": policy_id,
            "payload.client_idempotency_key": idem,
            "payload.submitted_by": user_id,
        })
    if row is None:
        return None
    return (row.get("payload") or {}).get("envelope_id")


async def _authorise(store, session: Session, policy_id: str, envelope_id: str,
                     action: str, needed: str) -> None:
    """Membership authority check (additive to the operator gate)."""
    policy = await scene.load_policy(store, session.tenant_id, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="policy_not_found")
    await require_authority(store, session, client_id=policy["client_id"], action=action,
                            needed=needed, policy_id=policy_id, envelope_id=envelope_id)


async def _park_for_broker(
    store, *, tenant_id: str, envelope_id: str, policy: dict,
    stage: str, mismatch: dict, gate_reason: str, review: bool = True,
) -> None:
    """Park the envelope with a visible broker card. Never prices."""
    if review:
        await scene.emit_request_needs_human_review(
            store, tenant_id=tenant_id, envelope_id=envelope_id,
            policy_id=policy["policy_id"], stage=stage, mismatch=mismatch,
        )
    ref_type = scene.NEEDS_HUMAN_REVIEW if review else scene.ADVICE_CLASSIFIED
    try:
        await queue_enqueue(
            store, tenant_id=tenant_id, envelope_id=envelope_id,
            class_key="request_info",
            proposal_ref=(await scene.last_event_id_for_envelope(
                store, tenant_id, ref_type, envelope_id,
            )) or "",
            matter_id=None, client_id=policy["client_id"],
            gate_reason=gate_reason, confidence=None,
            routed_to_role="account_executive",
        )
    except Exception:
        pass


async def _park_if_unmatched(
    store, *, tenant_id: str, envelope_id: str, policy: dict,
    changes: list | None, stage: str,
) -> bool:
    """Made-up-field guard: park for broker review instead of pricing."""
    try:
        validate_requested_changes(policy, changes)
        return False
    except UnmatchedPolicyField as e:
        await _park_for_broker(
            store, tenant_id=tenant_id, envelope_id=envelope_id, policy=policy,
            stage=stage, mismatch=e.as_payload(), gate_reason="unmatched_policy_field",
        )
        return True


async def _park_if_not_f_or_g(
    store, *, tenant_id: str, envelope_id: str, policy: dict,
    raw_message: str, changes: list | None,
) -> bool:
    """Thin F/G/P/X boundary (rules only). P → broker review, X → declined."""
    verdict = classify(raw_message, {"requested_changes": changes or []}, policy)
    await scene.emit_advice_boundary_classified(
        store, tenant_id=tenant_id, envelope_id=envelope_id,
        policy_id=policy["policy_id"], verdict=verdict,
    )
    if verdict["tier"] in ("F", "G"):
        return False
    is_p = verdict["tier"] == "P"
    await _park_for_broker(
        store, tenant_id=tenant_id, envelope_id=envelope_id, policy=policy,
        stage="advice_boundary", review=is_p,
        mismatch={"field_path": "advice_boundary", "attempted_value": None,
                  "reason": verdict["reason"], "rule_id": verdict["rule_id"]},
        gate_reason="personal_advice_required" if is_p else "out_of_scope",
    )
    return True


async def _once(coro) -> None:
    """Run an emit whose idempotency key may already be in the ledger (resumed chain)."""
    from app.kernel.trail_mongo import DuplicateIdempotencyKey
    try:
        await coro
    except DuplicateIdempotencyKey:
        pass


async def _test_pause(point: str) -> None:
    """Non-production hook so a restart can be tested at a precise step."""
    secs = os.environ.get(f"INTAKE_TEST_PAUSE_{point.upper()}_S")
    if secs and (os.environ.get("ENVIRONMENT") or "").lower() != "production":
        await asyncio.sleep(float(secs))


async def _run_intake_placement_async(
    store, *, tenant_id: str, user_id: str, envelope_id_str: str,
    policy_id: str, policy: dict, raw_message: str,
    state: dict | None = None, save=None,
) -> None:
    """Resumable chain (R1.1 job): intAIke → gates → plAIce → MOCKED carrier → queue card.

    Each completed step is recorded in `state` (persisted on the job row by `save`), so a
    restarted worker resumes from the last completed step without repeating ledger events.
    Failures raise to the job runner (retry with backoff, then dead-letter for Olly); the
    envelope stays parked in `ClientChangeRequested` meanwhile — silence parks.
    """
    state = state if state is not None else {}

    async def mark(step: str, value=True) -> None:
        state[step] = value
        if save:
            await save(step, value)

    envelope_id = uuid.UUID(envelope_id_str)
    brain = Brain(store)
    if "intake" not in state:
        intake_envelope = ContextEnvelope(
            envelope_id=envelope_id,
            tenant_id=uuid.UUID(tenant_id), matter_id=None,
            client_id=uuid.UUID(policy["client_id"]),
            class_key="request_info",
            intent="parse client change request",
            inputs={"raw_message": raw_message, "policy_id": policy_id,
                    "context_events_summary": "client-initiated change request"},
            context_events=[], risk_schema_refs=[], tenant_config={},
            budget=InvocationBudget(max_input_tokens=4000, max_output_tokens=1500, max_wall_ms=45000),
            caller=Actor(kind="human", id=user_id, name="client_owner"),
            ts=datetime.now(timezone.utc),
        )
        intake_out = await brain.invoke(intake_envelope, "intake")
        intake_payload = intake_out.payload or {}
        if intake_payload.get("proposed_changes"):
            intake_payload["proposed_changes"] = normalize_vehicle_ops(policy, intake_payload["proposed_changes"])
        await mark("intake", {"payload": intake_payload, "class_key": intake_out.class_key})
    intake_payload = state["intake"]["payload"]
    if "gates" not in state:
        parked = await _park_if_unmatched(
            store, tenant_id=tenant_id, envelope_id=envelope_id_str,
            policy=policy, changes=intake_payload.get("proposed_changes"), stage="intake",
        ) or await _park_if_not_f_or_g(
            store, tenant_id=tenant_id, envelope_id=envelope_id_str,
            policy=policy, raw_message=raw_message, changes=intake_payload.get("proposed_changes"),
        )
        await mark("gates", "parked" if parked else "ok")
    if state["gates"] == "parked":
        return
    await _test_pause("after_classify")
    proposed_class = state["intake"]["class_key"] or intake_payload.get("class_proposed") or ""
    proposal_ref = (await scene.last_event_id_for_envelope(store, tenant_id, "AgentOutputProduced", envelope_id_str)) or ""
    if proposed_class not in PLACEMENT_ELIGIBLE_CLASSES:
        if "card" not in state:
            await queue_enqueue(
                store, tenant_id=tenant_id, envelope_id=envelope_id_str,
                class_key=proposed_class if proposed_class in INTAKE_ELIGIBLE_CLASSES else "request_info",
                proposal_ref=proposal_ref, matter_id=None, client_id=policy["client_id"],
                gate_reason="intake_only", confidence=None, routed_to_role="account_executive",
            )
            await mark("card")
        return

    if "placement" not in state:
        placement_envelope = ContextEnvelope(
            envelope_id=envelope_id,
            tenant_id=uuid.UUID(tenant_id), matter_id=None,
            client_id=uuid.UUID(policy["client_id"]),
            class_key=proposed_class,
            intent="draft submission for market",
            inputs={"intake_output": intake_payload, "policy_current": policy,
                    "context_events_summary": "client-initiated change request"},
            context_events=[], risk_schema_refs=[], tenant_config={},
            budget=InvocationBudget(max_input_tokens=8000, max_output_tokens=3000, max_wall_ms=60000),
            caller=Actor(kind="human", id=user_id, name="client_owner"),
            ts=datetime.now(timezone.utc),
        )
        placement_out = await brain.invoke(placement_envelope, "placement")
        plc = placement_out.payload or {}
        sub = plc.get("structured_submission") or {}
        if sub.get("requested_changes"):
            sub["requested_changes"] = normalize_vehicle_ops(policy, sub["requested_changes"])
        await mark("placement", plc)
    plc_payload = state["placement"]
    if "placement_gate" not in state:
        parked = await _park_if_unmatched(
            store, tenant_id=tenant_id, envelope_id=envelope_id_str, policy=policy, stage="placement",
            changes=(plc_payload.get("structured_submission") or {}).get("requested_changes"),
        )
        await mark("placement_gate", "parked" if parked else "ok")
    if state["placement_gate"] == "parked":
        return
    if "submission" not in state:
        await _once(scene.emit_submission_drafted(
            store, tenant_id=tenant_id, envelope_id=envelope_id_str, policy_id=policy_id,
            submission_summary=plc_payload.get("submission_summary"),
            structured_submission=plc_payload.get("structured_submission") or {},
            recommended_wording_ref=plc_payload.get("recommended_wording_ref"),
        ))
        await mark("submission")
    if "quote" not in state:
        quote = await submit_endorsement(envelope_id=envelope_id_str, submission=plc_payload, policy_current=policy)
        await _once(scene.emit_carrier_quoted(store, tenant_id=tenant_id, envelope_id=envelope_id_str,
                                              policy_id=policy_id, quote=quote))
        await mark("quote", quote)
    quote = state["quote"]
    if "ready" not in state:
        await _once(scene.emit_endorsement_quote_ready(store, tenant_id=tenant_id, envelope_id=envelope_id_str,
                                                       policy_id=policy_id, quote_id=quote["quote_id"]))
        await mark("ready")
    if "card" not in state:
        await queue_enqueue(
            store, tenant_id=tenant_id, envelope_id=envelope_id_str, class_key=proposed_class,
            proposal_ref=proposal_ref, matter_id=None, client_id=policy["client_id"],
            gate_reason="awaiting_operator_class_shadow_authoritative", confidence=None,
            routed_to_role="account_executive",
        )
        await mark("card")


async def intake_job(store, job: dict) -> None:
    from app.kernel import jobs
    p = job["payload"]
    policy = await scene.load_policy(store, job["tenant_id"], p["policy_id"])
    if not policy:
        raise RuntimeError("policy not found")

    async def save(step, value):
        await jobs.save_state(store, job, step, value)
    await _run_intake_placement_async(
        store, tenant_id=job["tenant_id"], user_id=p["user_id"], envelope_id_str=p["envelope_id"],
        policy_id=p["policy_id"], policy=policy, raw_message=p["raw_message"],
        state=dict(p.get("state") or {}), save=save,
    )


@router.post("/{policy_id}/request-change")
async def request_change(
    policy_id: str,
    request: Request,
    body: RequestChangeBody = Body(...),
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    """Accept-fast: emit ClientChangeRequested, return <2s, run chain async.

    Idempotency: if the client resubmits with the same
    `idempotency_key` within the lifetime of the tenant's event log,
    we return the EXISTING envelope_id. No new envelope, no duplicate
    queue cards (R1 close-out — user prod verification 2026-10-02).
    """
    store = _store(request)
    policy = await scene.load_policy(store, session.tenant_id, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="policy_not_found")
    await require_authority(store, session, client_id=policy["client_id"], action="request_change_attempt",
                            needed="can_request_change", policy_id=policy_id)

    tenant_id = session.tenant_id
    idem = (body.idempotency_key or "").strip() or None
    if idem:
        existing = await _find_existing_envelope_by_idempotency(
            store, tenant_id, policy_id, session.user_id, idem,
        )
        if existing:
            return {
                "state": "received",
                "envelope_id": existing,
                "idempotency_key": idem,
                "deduplicated": True,
            }

    envelope_id = uuid.uuid4()
    try:
        await scene.emit_client_change_requested(
            store, tenant_id=tenant_id, user_id=session.user_id,
            envelope_id=str(envelope_id), policy_id=policy_id,
            client_id=policy["client_id"], raw_message=body.raw_message,
            client_idempotency_key=idem,
        )
    except Exception as e:
        # Mongo unique-index collision on the idempotency_key — a
        # concurrent sibling request already won the insert. Look up
        # the winner by the same idem key and return its envelope.
        if idem and "duplicate" in str(e).lower():
            existing = await _find_existing_envelope_by_idempotency(
                store, tenant_id, policy_id, session.user_id, idem,
            )
            if existing:
                return {
                    "state": "received",
                    "envelope_id": existing,
                    "idempotency_key": idem,
                    "deduplicated": True,
                }
        raise

    from app.kernel import jobs
    await jobs.enqueue(store, "intake.chain", {
        "envelope_id": str(envelope_id), "policy_id": policy_id, "user_id": session.user_id,
        "raw_message": body.raw_message, "state": {},
    }, key=f"intake:{envelope_id}", tenant_id=tenant_id, max_attempts=3)

    return {
        "state": "received",
        "envelope_id": str(envelope_id),
        "idempotency_key": idem,
        "deduplicated": False,
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
    env = next(((e.get("payload") or {}).get("envelope_id") for e in events
                if (e.get("payload") or {}).get("envelope_id")), None)
    if env:  # R0.11 follow-up: broker decisions on the envelope carry no policy_id
        extra = [e for e in await scene_mongo.envelope_events_without_policy(store, session.tenant_id, env)
                 if e["type"] != "Approval"]  # broker-side lineage Approval is not the client's
        events = sorted(events + extra, key=lambda e: e.get("seq") or 0, reverse=True)
    return scene.compute_status(events)


async def _load_payable(store, tenant_id: str, policy_id: str,
                        envelope_id: str) -> tuple[dict, dict]:
    policy = await scene.load_policy(store, tenant_id, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="policy_not_found")
    quote = await scene.find_carrier_quote(store, tenant_id, envelope_id)
    if not quote:
        raise HTTPException(status_code=404, detail="quote_not_found")
    await _require_client_approval(store, tenant_id, envelope_id)
    return policy, quote


async def _details_for(store, tenant_id: str, policy: dict, quote: dict, envelope_id: str) -> dict:
    """R1.5: a payable with its own CRN; real trust details once the Operator sets them, MOCKED until then."""
    from app.kernel import payments
    p = await payments.ensure_payable(store, tenant_id, envelope_id=envelope_id, policy_id=policy["policy_id"],
                                      amount=quote.get("premium_change_amount") or "0",
                                      currency=quote.get("premium_currency") or policy["premium_currency"])
    return payments.details(p, await payments.get_settings(store, tenant_id))


@router.api_route("/{policy_id}/pay/{envelope_id}", methods=["GET", "POST"])
async def pay(
    policy_id: str,
    envelope_id: str,
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    """MOCKED payment details (BPAY + EFT). Read-only: emits no events."""
    store = _store(request)
    policy, quote = await _load_payable(
        store, session.tenant_id, policy_id, envelope_id,
    )
    advised = await _find_envelope_event(
        store, session.tenant_id, envelope_id, scene.PAYMENT_ADVISED,
    )
    return {
        "state": "awaiting_signature" if advised else "awaiting_payment",
        "envelope_id": envelope_id,
        "payment_details": await _details_for(store, session.tenant_id, policy, quote, envelope_id),
        "payment_advised": bool(advised),
    }


class PaidBody(BaseModel):
    paid_at: str | None = Field(default=None, max_length=40)


@router.post("/{policy_id}/paid/{envelope_id}")
async def paid(
    policy_id: str,
    envelope_id: str,
    request: Request,
    body: PaidBody | None = Body(default=None),
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    """Client taps "I've paid this" → one `payment.advised` ledger event.

    Advice only: nothing is marked Paid (M3-5). Idempotent per envelope.
    """
    if session.active_role not in ("client_owner", "client_contact"):
        raise HTTPException(status_code=403, detail="client_role_required")
    store = _store(request)
    tenant_id = session.tenant_id
    await _authorise(store, session, policy_id, envelope_id, "pay_attempt", "can_pay")
    policy, quote = await _load_payable(store, tenant_id, policy_id, envelope_id)
    row = await _find_envelope_event(
        store, tenant_id, envelope_id, scene.PAYMENT_ADVISED,
    )
    deduplicated = row is not None
    if not deduplicated:
        details = await _details_for(store, tenant_id, policy, quote, envelope_id)
        paid_at = (body.paid_at if body and body.paid_at
                   else datetime.now(timezone.utc).isoformat(timespec="seconds"))
        try:
            await scene.emit_payment_advised(
                store, tenant_id=tenant_id, envelope_id=envelope_id,
                policy_id=policy_id, user_id=session.user_id,
                actor_role=session.active_role, amount=details["amount"],
                currency=details["currency"], method="bpay", paid_at=paid_at,
            )
        except DuplicateIdempotencyKey:
            # Concurrent double-tap lost the idempotency-key race.
            deduplicated = True
        row = await _find_envelope_event(
            store, tenant_id, envelope_id, scene.PAYMENT_ADVISED,
        )
    from app.kernel import payments
    await payments.mark_advised(store, tenant_id, envelope_id)  # Advised only — Received comes from the bank feed
    return {
        "state": "awaiting_signature",
        "envelope_id": envelope_id,
        "deduplicated": deduplicated,
        "ledger_event": {
            "event_id": row["event_id"],
            "type": scene.PAYMENT_ADVISED,
            "seq": row["seq"],
            "mocked": True,
            "payload": row["payload"],
        },
    }


@router.post("/{policy_id}/sign/{envelope_id}")
async def sign(
    policy_id: str,
    envelope_id: str,
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    store = _store(request)
    tenant_id = session.tenant_id
    await _authorise(store, session, policy_id, envelope_id, "sign_attempt", "can_sign")
    await _require_predecessor(
        store, tenant_id, envelope_id, [scene.PAYMENT_ADVISED],
    )
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


@router.post("/{policy_id}/approve/{envelope_id}")
async def approve(
    policy_id: str,
    envelope_id: str,
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    """Client-side approval of the mocked-carrier quote.

    Emits an `Approval` event so `execute_endorsement` finds a complete
    lineage on finalise. Carries `gate_reason="carrier_mock_passed"` per
    R1 #2 audit requirement — the operator queue card gate is bypassed
    for the SIRC demo path because the carrier response is mocked. The
    real (non-mocked) path routes via the operator queue and emits a
    principal-signed Approval via `resolve_queue_card`.
    """
    store = _store(request)
    tenant_id = session.tenant_id
    if session.active_role not in ("client_owner", "client_contact"):
        raise HTTPException(status_code=403, detail="client_role_required")
    await _authorise(store, session, policy_id, envelope_id, "approve_attempt", "can_approve")
    policy = await scene.load_policy(store, tenant_id, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail="policy_not_found")
    quote = await scene.find_carrier_quote(store, tenant_id, envelope_id)
    if not quote:
        raise HTTPException(status_code=404, detail="quote_not_found")
    await _require_predecessor(
        store, tenant_id, envelope_id,
        ["EndorsementQuoteReady", "OperatorApproval"],
    )

    deduplicated = False
    async with use_tenant(tenant_id):
      try:
        await append_via_dispatch(
            store, tenant_id, "Approval",
            actor={"kind": "human", "id": session.user_id, "name": "client_owner"},
            payload={
                "envelope_id": envelope_id,
                "policy_id": policy_id,
                "client_id": policy["client_id"],
                "verdict": "approve",
                "gate_reason": "operator_class_shadow_authoritative",
                "mocked": True,
                "quote_id": quote.get("quote_id"),
            },
            idempotency_key=f"client:approval:{envelope_id}",
            mocked=True,
        )
      except DuplicateIdempotencyKey:
        deduplicated = True
    return {
        "state": "awaiting_payment",
        "envelope_id": envelope_id,
        "deduplicated": deduplicated,
        "quote_id": quote.get("quote_id"),
        "gate_reason": "operator_class_shadow_authoritative",
    }


@router.post("/{policy_id}/finalise/{envelope_id}")
async def finalise(
    policy_id: str,
    envelope_id: str,
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    """Run endorsement execution + certificate regeneration."""
    from app.kernel.endorsement import EndorsementLineageIncomplete
    store = _store(request)
    await _authorise(_store(request), session, policy_id, envelope_id, "finalise_attempt", "can_sign")
    tenant_id = session.tenant_id
    try:
        result = await execute_endorsement(
            store=store, tenant_id=tenant_id,
            envelope_id=envelope_id, policy_id=policy_id,
            approver_actor={"kind": "system", "id": "endorsement", "name": "endorsement"},
        )
    except EndorsementLineageIncomplete as e:
        # Wrap the internal guard as a structured 409 so callers get a
        # named code + human-readable detail (R1 #2 tester follow-up).
        raise LineageIncomplete(str(e))
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
