"""Operator queue routes — /api/o/queue and Decision Card actions (R5c-4b — Mongo-only)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
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


def _cite_label(kind, ref: str) -> str:
    """Plain words for a structured citation (no field paths on screen)."""
    tail = ref.split(":", 1)[-1].split(".")[-1].replace("[]", "").replace("[0]", "")
    words = tail.replace("_", " ").replace("-", " ").strip()
    for agent in ("intake ", "placement "):
        words = words.removeprefix(agent)
    src = {"risk_schema": "Policy schedule", "event": "Client request"}.get(kind or "", "Source")
    return f"{src} — {words}"


def _utc_iso(v):
    from datetime import datetime, timezone
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
    return v


@router.get("")
async def list_queue(
    request: Request,
    session: Session = Depends(require_operator_session),
) -> dict:
    _ensure_queue_role(session)
    store = _store(request)
    cards = await workflow.fetch_pending_queue(store, session.tenant_id)
    from app.db.tenant_safe import wrap
    from app.kernel.notify import _client_id
    from app.kernel.tenant_ctx import use_tenant
    safe = wrap(store)
    async with use_tenant(session.tenant_id):
        cids = {c["envelope_id"]: c.get("client_id") or await _client_id(safe, {"envelope_id": c["envelope_id"]}) for c in cards}
        names = {x["client_id"]: x.get("display_name") async for x in
                 safe.clients.find({"client_id": {"$in": [v for v in cids.values() if v]}}, {"client_id": 1, "display_name": 1})}
    for c in cards:
        if not c.get("client_display"):
            c["client_display"] = names.get(cids.get(c["envelope_id"]))
        c.setdefault("matter_display", None)
    from app.kernel.event_text import staff_out
    return {"cards": staff_out(session, cards), "count": len(cards)}


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
        if not proposal:
            # R0.6: spine cards cite through the latest agent output on the envelope.
            p_ev = await safe.events.find_one(
                {"type": "AgentOutputProduced", "payload.envelope_id": envelope_id}, sort=[("seq", -1)])
            if p_ev:
                proposal = {"event_id": p_ev["event_id"], "ts": p_ev["ts"], "type": p_ev["type"],
                            "actor": p_ev["actor"], "payload": p_ev["payload"]}
        raw_cites = list((proposal.get("payload") or {}).get("citations", []))
        citation_refs = [c for c in raw_cites if isinstance(c, str)]
        for c in raw_cites:
            if isinstance(c, dict) and c.get("ref"):
                evidence.append({
                    "event_id": f"cite:{c['ref']}", "ts": proposal["ts"], "type": "Citation",
                    "actor": proposal["actor"],
                    "payload": {"kind": c.get("kind"), "ref": c["ref"],
                                "label": _cite_label(c.get("kind"), c["ref"])},
                })
        if citation_refs:
            async for e in safe.events.find(
                {"event_id": {"$in": citation_refs}}
            ).sort("seq", 1):
                evidence.append({
                    "event_id": e["event_id"], "ts": e["ts"],
                    "type": e["type"], "actor": e["actor"],
                    "payload": e["payload"],
                })
        from app.kernel.event_text import describe, raw_ok, staff_out
        for ev in evidence:
            ev.update(describe(ev["type"], ev["payload"]) if ev["type"] != "Citation" else
                      {"text": ev["payload"].get("label") or "Cited source", "link": None})
        if not raw_ok(session):
            evidence = [{"ts": e["ts"], "type": "Citation" if e["type"] == "Citation" else "Event", "text": e["text"], "link": e["link"],
                         "kind": (e["payload"] or {}).get("kind") if e["type"] == "Citation" else None} for e in evidence]
    conf_obj = (proposal.get("payload") or {}).get("confidence") or {}
    components = conf_obj.get("components") or {}
    scalar = conf_obj.get("scalar")
    if scalar is None and card.get("confidence") is not None:
        scalar = float(card["confidence"])
    resolution = None
    if card.get("resolved_at"):
        async with use_tenant(session.tenant_id):
            r_ev = await safe.events.find_one({"event_id": card.get("resolved_by_event")}) or {}
            actor = r_ev.get("actor") or {}
            u = await safe.users.find_one({"user_id": actor.get("id")}) if actor.get("id") else None
        resolution = {
            "event_type": r_ev.get("type"),
            "verdict": (r_ev.get("payload") or {}).get("verdict") or card.get("resolution"),
            "actor_name": (u or {}).get("display_name") or actor.get("name"),
            "resolved_at": _utc_iso(card.get("resolved_at")),
        }
    return {
        "resolution": resolution,
        "card": staff_out(session, card),
        "proposal": staff_out(session, scrub(proposal)) if raw_ok(session) else
        {"ts": proposal.get("ts"), "type": proposal.get("type"),
         "stage": (proposal.get("payload") or {}).get("stage"),
         "citations": staff_out(session, scrub((proposal.get("payload") or {}).get("citations") or [])),
         "mismatch": staff_out(session, scrub((proposal.get("payload") or {}).get("mismatch"))),
         "output": staff_out(session, scrub((proposal.get("payload") or {}).get("output"))),
         "claim_id": (proposal.get("payload") or {}).get("claim_id")},
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


class InsurerQuoteBody(BaseModel):
    premium_change_amount: str = Field(max_length=20)
    premium_new_annual: str | None = Field(default=None, max_length=20)
    insurer: str | None = Field(default=None, max_length=80)
    reference: str | None = Field(default=None, max_length=80)
    terms: str | None = Field(default=None, max_length=500)


@router.post("/{envelope_id}/insurer-quote")
async def insurer_quote(envelope_id: str, request: Request, body: InsurerQuoteBody,
                        session: Session = Depends(require_operator_session)) -> dict:
    """R1.6 manual_only tenants: the AE keys in the real insurer quote for a parked change; nothing is mocked."""
    from decimal import Decimal, InvalidOperation

    from app.kernel import scene
    from app.routes.c_scene import MANUAL_QUOTE_STAGE
    if session.active_role not in ("ae", "principal"):
        raise HTTPException(status_code=403, detail="Only an AE or a Principal can enter an insurer quote.")
    store = _store(request)
    card = await workflow.fetch_queue_card(store, session.tenant_id, envelope_id)
    async with use_tenant(session.tenant_id):
        safe = wrap(store)
        parked = await safe.events.find_one({"type": "RequestNeedsHumanReview", "payload.envelope_id": envelope_id,
                                             "payload.stage": MANUAL_QUOTE_STAGE})
        done = await safe.events.find_one({"type": "CarrierQuoted", "payload.envelope_id": envelope_id})
        user = await safe.users.find_one({"user_id": session.user_id}) or {}
    if not card or card.get("resolved_at") or not parked:
        raise HTTPException(status_code=404, detail="No open 'Enter insurer quote' card for this request.")
    if done:
        raise HTTPException(status_code=409, detail="A quote is already recorded for this request.")
    policy = await scene.load_policy(store, session.tenant_id, parked["payload"]["policy_id"])
    try:
        delta = Decimal(body.premium_change_amount.replace(",", "").replace("$", "")).quantize(Decimal("0.01"))
        new = (Decimal(body.premium_new_annual.replace(",", "").replace("$", "")) if body.premium_new_annual
               else Decimal(str(policy.get("premium_amount") or 0)) + delta).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise HTTPException(status_code=422, detail="Enter the premium change as an amount, e.g. 85.00.") from None
    quote = {"quote_id": str(uuid.uuid4()), "carrier_name": body.insurer or policy.get("insurer_display"),
             "premium_change_amount": str(delta), "premium_new_annual": str(new),
             "premium_currency": policy.get("premium_currency") or "AUD", "terms": body.terms or "", "conditions": [],
             "insurer_reference": body.reference, "quoted_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
             "mocked": False, "data_basis": f"manual entry by {user.get('display_name') or 'a broker'}"}
    actor = {"kind": "human", "id": session.user_id, "name": session.active_role}
    async with use_tenant(session.tenant_id):
        await append_via_dispatch(store, session.tenant_id, "CarrierQuoted", actor=actor,
                                  payload={"envelope_id": envelope_id, "policy_id": policy["policy_id"], **quote},
                                  idempotency_key=f"carrier:quote:{envelope_id}")
    await scene.emit_endorsement_quote_ready(store, tenant_id=session.tenant_id, envelope_id=envelope_id,
                                             policy_id=policy["policy_id"], quote_id=quote["quote_id"])
    return {"quote": quote, "next": "Approve the card to send it to the client."}


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
