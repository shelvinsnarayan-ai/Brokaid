"""Client-facing claim scene endpoints (R5c-4b — Mongo-only)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.sessions import Session
from app.db.tenant_safe import wrap as _wrap_safe
from app.kernel import claims_scene, workflow
from app.kernel.agents.claims_stub import DeterministicClaimsStubProvider
from app.kernel.brain import Brain
from app.kernel.contracts.models import Actor, ContextEnvelope, InvocationBudget
from app.kernel.mocks.insurer_lodgement import lodge_claim
from app.kernel.notifications import render_and_send
from app.kernel.tenant_ctx import use_tenant
from app.kernel.authority import require_client_authority
from app.middleware.session import require_session


router = APIRouter(prefix="/api/c")


class FNOLBody(BaseModel):
    incident_date: str | None = Field(default=None, description="YYYY-MM-DD")
    description: str = Field(..., min_length=8, max_length=4000)
    policy_id: str | None = None


def _store(request: Request):
    s = getattr(request.app.state, "mongo_db", None)
    if s is None:
        raise HTTPException(status_code=503, detail="mongo_store_unavailable")
    return s


def _parse_iso_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _iso(v):
    return (
        v.isoformat(timespec="microseconds")
        if hasattr(v, "isoformat") else v
    )


@router.post("/claims")
async def submit_fnol(
    request: Request,
    body: FNOLBody = Body(...),
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    """FNOL entry. Runs the full claim scene end-to-end."""
    store = _store(request)
    await require_client_authority(store, session, action="claim_attempt", needed="can_claim")
    tenant_id = session.tenant_id
    client = await claims_scene.load_client_for_user(
        store, tenant_id, session.user_id,
    )
    if not client:
        raise HTTPException(status_code=404, detail="client_not_linked")

    envelope_id = uuid.uuid4()
    incident_dt = _parse_iso_date(body.incident_date)

    await claims_scene.emit_client_request_received(
        store, tenant_id=tenant_id, user_id=session.user_id,
        envelope_id=str(envelope_id), client_id=client["client_id"],
        raw_message=body.description, policy_id=body.policy_id,
        incident_date=body.incident_date,
    )

    env = ContextEnvelope(
        envelope_id=envelope_id,
        tenant_id=uuid.UUID(tenant_id),
        matter_id=None,
        client_id=uuid.UUID(client["client_id"]),
        class_key="claim_admin",
        intent="triage FNOL",
        inputs={
            "raw_fnol_message": body.description,
            "matter_kind": "claim",
            "client_context_summary": client["display_name"],
        },
        context_events=[],
        risk_schema_refs=[],
        tenant_config={},
        budget=InvocationBudget(
            max_input_tokens=2000, max_output_tokens=800, max_wall_ms=5000,
        ),
        caller=Actor(kind="human", id=session.user_id, name="client_owner"),
        ts=datetime.now(timezone.utc),
    )
    brain = Brain(
        store,
        resolver=lambda name: DeterministicClaimsStubProvider(),
    )
    triage_out = await brain.invoke(env, "claims")
    triage = triage_out.payload or {}

    await claims_scene.emit_claim_triaged(
        store, tenant_id=tenant_id,
        envelope_id=str(envelope_id), client_id=client["client_id"],
        triage=triage,
    )

    claim_id = str(uuid.uuid4())
    claim_ref = await claims_scene.next_claim_reference(store, tenant_id)
    created_ev = await claims_scene.emit_claim_created(
        store, tenant_id=tenant_id,
        envelope_id=str(envelope_id), claim_id=claim_id,
        claim_reference=claim_ref, client_id=client["client_id"],
        policy_id=body.policy_id,
        claim_type=triage.get("claim_type"),
        urgency=triage.get("urgency"),
        description=body.description,
        incident_date=body.incident_date,
    )
    await claims_scene.insert_claim_row(
        store, tenant_id,
        claim_id=claim_id, client_id=client["client_id"],
        policy_id=(body.policy_id or None),
        claim_reference=claim_ref, incident_date=incident_dt,
        description=body.description,
        claim_type=triage.get("claim_type"),
        urgency=triage.get("urgency"),
        opened_by_event=created_ev["event_id"],
    )
    await claims_scene.append_status_update(
        store, tenant_id,
        claim_id=claim_id, event_id=created_ev["event_id"],
        kind="triaged",
        summary=(triage.get("structured_summary") or "Triage complete."),
        mocked=False,
    )

    await claims_scene.emit_insurer_lodgement_requested(
        store, tenant_id=tenant_id,
        envelope_id=str(envelope_id),
        claim_id=claim_id, claim_reference=claim_ref,
    )
    lodgement = await lodge_claim({
        "claim_id": claim_id,
        "claim_reference": claim_ref,
        "claim_type": triage.get("claim_type"),
        "description": body.description,
    })
    ack_ev = await claims_scene.emit_insurer_lodgement_ack(
        store, tenant_id=tenant_id,
        envelope_id=str(envelope_id),
        claim_id=claim_id, claim_reference=claim_ref,
        lodgement=lodgement,
    )
    await claims_scene.mark_claim_lodged(store, tenant_id, claim_id)
    await claims_scene.append_status_update(
        store, tenant_id,
        claim_id=claim_id, event_id=ack_ev["event_id"],
        kind="lodged",
        summary=(
            f"Lodged with {lodgement['insurer_display']} · "
            f"ref {lodgement['insurer_reference']}"
        ),
        mocked=True,
    )

    if triage.get("urgency") == "critical" or triage.get("requires_coverage_check"):
        try:
            await workflow.enqueue(
                store,
                tenant_id=tenant_id,
                envelope_id=str(envelope_id),
                class_key="claim_admin",
                proposal_ref=created_ev["event_id"],
                matter_id=None,
                client_id=client["client_id"],
                gate_reason=(
                    "coverage_check_required"
                    if triage.get("requires_coverage_check")
                    else "urgency_critical"
                ),
                confidence=0.7,
                routed_to_role="principal",
            )
            await claims_scene.emit_queue_card_enqueued(
                store, tenant_id=tenant_id,
                envelope_id=str(envelope_id), claim_id=claim_id,
            )
        except Exception:
            pass

    recipient_email = await claims_scene.load_recipient_email(
        store, tenant_id, client["client_id"],
    )
    notif = await render_and_send(
        store=store, tenant_id=tenant_id, kind="claim_lodged",
        payload={
            "claim_reference": claim_ref,
            "insurer_display": lodgement["insurer_display"],
            "client_display": client["display_name"],
            "brokerage_display": client.get("brokerage_display"),
            "summary": (
                f"Your claim has been lodged with "
                f"{lodgement['insurer_display']} "
                f"(reference {lodgement['insurer_reference']}). "
                "We'll update you as it moves."
            ),
        },
        recipient={"client_id": client["client_id"], "email": recipient_email},
        source_event_id=ack_ev["event_id"],
    )

    return {
        "state": "lodged",
        "claim_id": claim_id,
        "claim_reference": claim_ref,
        "envelope_id": str(envelope_id),
        "triage": triage,
        "lodgement": {
            "insurer_display": lodgement["insurer_display"],
            "insurer_reference": lodgement["insurer_reference"],
            "mocked": True,
        },
        "notification": {
            "dev_mode": notif["dev_mode"],
            "subject": notif["subject"],
        },
        "next_steps": [
            "Watch this claim's timeline for insurer updates.",
            "You can message your broker any time via the Ask channel.",
        ],
    }


@router.get("/claims")
async def list_claims(
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    store = _store(request)
    tenant_id = session.tenant_id
    client = await claims_scene.load_client_for_user(
        store, tenant_id, session.user_id,
    )
    if not client:
        raise HTTPException(status_code=404, detail="client_not_linked")
    safe = _wrap_safe(store)
    out: list[dict[str, Any]] = []
    async with use_tenant(tenant_id):
        cursor = safe.claims.find(
            {"client_id": client["client_id"]}
        ).sort("opened_at", -1).limit(100)
        async for r in cursor:
            out.append({
                "claim_id": r["claim_id"],
                "claim_reference": r.get("claim_reference"),
                "claim_type": r.get("claim_type"),
                "urgency": r.get("urgency"),
                "status": r.get("status"),
                "opened_at": _iso(r.get("opened_at")),
                "resolved_at": _iso(r.get("resolved_at")),
                "description_preview": (r.get("description") or "")[:140],
            })
    return {"claims": out}


@router.get("/claims/{claim_id}")
async def get_claim(
    claim_id: str, request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    store = _store(request)
    tenant_id = session.tenant_id
    client = await claims_scene.load_client_for_user(
        store, tenant_id, session.user_id,
    )
    if not client:
        raise HTTPException(status_code=404, detail="client_not_linked")
    safe = _wrap_safe(store)
    async with use_tenant(tenant_id):
        claim = await safe.claims.find_one({
            "claim_id": claim_id, "client_id": client["client_id"],
        })
        if not claim:
            raise HTTPException(status_code=404, detail="claim_not_found")
        timeline = []
        async for r in safe.proj_claim_status_updates.find(
            {"claim_id": claim_id}
        ).sort("seq", -1):
            timeline.append({
                "seq": r.get("seq"),
                "ts": _iso(r.get("ts")),
                "kind": r.get("kind"),
                "summary": r.get("summary"),
                "mocked": r.get("mocked", False),
            })
    return {
        "claim": {
            "claim_id": claim["claim_id"],
            "claim_reference": claim.get("claim_reference"),
            "claim_type": claim.get("claim_type"),
            "urgency": claim.get("urgency"),
            "status": claim.get("status"),
            "opened_at": _iso(claim.get("opened_at")),
            "resolved_at": _iso(claim.get("resolved_at")),
            "description": claim.get("description"),
            "policy_id": claim.get("policy_id"),
        },
        "timeline": timeline,
    }


@router.get("/notifications")
async def list_notifications(
    request: Request,
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    store = _store(request)
    tenant_id = session.tenant_id
    client = await claims_scene.load_client_for_user(
        store, tenant_id, session.user_id,
    )
    if not client:
        raise HTTPException(status_code=404, detail="client_not_linked")
    safe = _wrap_safe(store)
    out: list[dict[str, Any]] = []
    async with use_tenant(tenant_id):
        cursor = safe.proj_notifications.find(
            {"client_id": client["client_id"]}
        ).sort("ts", -1).limit(50)
        async for r in cursor:
            out.append({
                "notification_id": r["notification_id"],
                "ts": _iso(r.get("ts")),
                "kind": r.get("kind"),
                "subject": r.get("subject"),
                "body_text": r.get("body_text"),
                "read": r.get("read_at") is not None,
                "source_event_id": r.get("source_event_id"),
            })
    return {"notifications": out}
