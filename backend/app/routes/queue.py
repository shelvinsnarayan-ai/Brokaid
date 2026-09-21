"""Operator queue routes — /api/o/queue and Decision Card actions (R5c-4b — Mongo-only)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel import compliance, workflow
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.middleware.session import require_operator_session
from app.routes._scrub import scrub


router = APIRouter(prefix="/api/o/queue")


def _ensure_queue_role(session: Session) -> None:
    """Queue is action-capable — data-only operator roles (auditor,
    pod_lead, account_manager, claims_manager, tenant_admin) are 403
    at the route, in addition to the login-gate that already blocks
    them at impersonation time."""
    from app.kernel.rbac import LOGIN_CAPABLE_ROLES
    if session.active_role not in LOGIN_CAPABLE_ROLES:
        raise HTTPException(status_code=403, detail="operator_action_role_required")


def _store(request: Request):
    s = getattr(request.app.state, "mongo_db", None)
    if s is None:
        raise HTTPException(status_code=503, detail="mongo_store_unavailable")
    return s


class ActionBody(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    edits: dict[str, Any] | None = None
    target_kind: str | None = "workflow"


@router.get("")
async def list_queue(
    request: Request,
    session: Session = Depends(require_operator_session),
) -> dict:
    _ensure_queue_role(session)
    store = _store(request)
    cards = await workflow.fetch_pending_queue(store, session.tenant_id)
    for c in cards:
        c.setdefault("client_display", None)
        c.setdefault("matter_display", None)
    return {"cards": cards, "count": len(cards)}


@router.get("/{envelope_id}")
async def get_card(
    envelope_id: str, request: Request,
    session: Session = Depends(require_operator_session),
) -> dict:
    store = _store(request)
    card = await workflow.fetch_queue_card(
        store, session.tenant_id, envelope_id,
    )
    if not card:
        raise HTTPException(status_code=404, detail="not_found")
    safe = wrap(store)
    proposal: dict = {}
    evidence: list[dict] = []
    async with use_tenant(session.tenant_id):
        if card.get("proposal_ref"):
            p_ev = await safe.events.find_one(
                {"event_id": card["proposal_ref"]}
            )
            if p_ev:
                proposal = {
                    "event_id": p_ev["event_id"],
                    "ts": p_ev["ts"], "type": p_ev["type"],
                    "actor": p_ev["actor"], "payload": p_ev["payload"],
                }
        citation_refs = list((proposal.get("payload") or {}).get("citations", []))
        if citation_refs:
            async for e in safe.events.find(
                {"event_id": {"$in": citation_refs}}
            ).sort("seq", 1):
                evidence.append({
                    "event_id": e["event_id"], "ts": e["ts"],
                    "type": e["type"], "actor": e["actor"],
                    "payload": e["payload"],
                })
    conf_obj = (proposal.get("payload") or {}).get("confidence") or {}
    components = conf_obj.get("components") or {}
    scalar = conf_obj.get("scalar")
    if scalar is None and card.get("confidence") is not None:
        scalar = float(card["confidence"])
    return {
        "card": card,
        "proposal": scrub(proposal),
        "evidence": scrub(evidence),
        "confidence": {
            "components": {
                "schema_validity": float(components.get("schema_validity", 0.0)),
                "completeness": float(components.get("completeness", 0.0)),
                "citation_existence": float(components.get("citation_existence", 0.0)),
                "class_agreement": float(components.get("class_agreement", 0.0)),
            },
            "scalar": None if scalar is None else float(scalar),
            "source": "computed",
        },
        "mocked_capabilities": (proposal.get("payload") or {}).get(
            "mocked_capabilities", []
        ),
    }


async def _emit_approval_and_resolve(
    store, session: Session, envelope_id: str, card: dict,
    verdict: str, edits: dict, reason: str | None,
) -> str:
    """Emit `OperatorApproval` (upstream gate for the client Approval),
    then the legacy `Approval` (kept for existing execute_endorsement
    lineage checks), then mark the queue card resolved.

    The `OperatorApproval` event is the AE / principal signing off
    that the class is authoritative to proceed while it is in Shadow
    Mode — per Build Brief v2.1 governance rules (no auto-unlock).
    Client `/api/c/matters/{p}/approve/{env}` requires this event to
    be present before it will emit its own client-owner Approval.
    """
    async with use_tenant(session.tenant_id):
        op_ev = await append_via_dispatch(
            store, session.tenant_id, "OperatorApproval",
            actor={"kind": "human", "id": session.user_id,
                   "name": session.active_role or "operator"},
            payload={
                "envelope_id": envelope_id,
                "class_key": card.get("class_key"),
                "verdict": verdict,
                "edits": edits or {},
                "reason": (reason or "")[:400],
                "gate_reason": "operator_class_shadow_authoritative",
            },
            idempotency_key=f"op:approval:{envelope_id}",
        )
        ev = await append_via_dispatch(
            store, session.tenant_id, "Approval",
            actor={"kind": "human", "id": session.user_id,
                   "name": session.active_role or "operator"},
            payload={
                "envelope_id": envelope_id,
                "class_key": card.get("class_key"),
                "verdict": verdict,
                "edits": edits or {},
                "reason": (reason or "")[:400],
                "gate_reason": "operator_class_shadow_authoritative",
                "upstream_operator_event": op_ev.get("event_id"),
            },
            idempotency_key=f"queue:approval:{envelope_id}",
        )
    await workflow.resolve_queue_card(
        store, session.tenant_id, envelope_id,
        resolved_by_event=ev["event_id"],
    )
    return ev["event_id"]


@router.post("/{envelope_id}/approve")
async def approve(
    envelope_id: str, request: Request,
    body: ActionBody = Body(default_factory=ActionBody),
    session: Session = Depends(require_operator_session),
) -> dict:
    _ensure_queue_role(session)
    store = _store(request)
    card = await workflow.fetch_queue_card(
        store, session.tenant_id, envelope_id,
    )
    if not card:
        raise HTTPException(status_code=404, detail="not_found")
    if card.get("resolved_at"):
        raise HTTPException(status_code=409, detail="already_resolved")
    class_key = card["class_key"]
    if class_key == "trust_money":
        raise HTTPException(
            status_code=501, detail="trust_money_mongo_not_implemented",
        )
    ev_id = await _emit_approval_and_resolve(
        store, session, envelope_id, card, "approve", {}, body.reason,
    )
    return {"state": "resolved", "approval_event_id": ev_id}


@router.post("/{envelope_id}/edit")
async def edit_and_approve(
    envelope_id: str, request: Request,
    body: ActionBody = Body(default_factory=ActionBody),
    session: Session = Depends(require_operator_session),
) -> dict:
    _ensure_queue_role(session)
    store = _store(request)
    card = await workflow.fetch_queue_card(store, session.tenant_id, envelope_id)
    if not card:
        raise HTTPException(status_code=404, detail="not_found")
    if card.get("resolved_at"):
        raise HTTPException(status_code=409, detail="already_resolved")
    ev_id = await _emit_approval_and_resolve(
        store, session, envelope_id, card, "edit",
        body.edits or {}, body.reason,
    )
    return {"state": "resolved", "approval_event_id": ev_id}


@router.post("/{envelope_id}/reject")
async def reject(
    envelope_id: str, request: Request,
    body: ActionBody = Body(default_factory=ActionBody),
    session: Session = Depends(require_operator_session),
) -> dict:
    _ensure_queue_role(session)
    store = _store(request)
    card = await workflow.fetch_queue_card(store, session.tenant_id, envelope_id)
    if not card:
        raise HTTPException(status_code=404, detail="not_found")
    if card.get("resolved_at"):
        raise HTTPException(status_code=409, detail="already_resolved")
    ev_id = await _emit_approval_and_resolve(
        store, session, envelope_id, card, "reject", {}, body.reason,
    )
    return {"state": "resolved", "approval_event_id": ev_id}


@router.post("/{envelope_id}/block")
async def block(
    envelope_id: str, request: Request,
    body: ActionBody = Body(default_factory=ActionBody),
    session: Session = Depends(require_operator_session),
) -> dict:
    store = _store(request)
    card = await workflow.fetch_queue_card(store, session.tenant_id, envelope_id)
    if not card:
        raise HTTPException(status_code=404, detail="not_found")
    if card.get("resolved_at"):
        raise HTTPException(status_code=409, detail="already_resolved")
    if session.active_role != "compliance":
        raise HTTPException(status_code=403, detail="compliance_only")
    block_id = await compliance.add_block(
        store, tenant_id=session.tenant_id,
        target_kind=body.target_kind or "workflow",
        target_id=envelope_id,
        reason=body.reason or "operator queue block",
        actor={"kind": "human", "id": session.user_id, "name": "compliance"},
    )
    safe = wrap(store)
    async with use_tenant(session.tenant_id):
        row = await safe.events.find_one(
            {"idempotency_key": f"compl-add:{block_id}"}
        )
    ev_id = row["event_id"] if row else None
    await workflow.resolve_queue_card(
        store, session.tenant_id, envelope_id,
        resolved_by_event=ev_id,
    )
    return {"state": "blocked", "block_id": block_id, "block_event_id": ev_id}
