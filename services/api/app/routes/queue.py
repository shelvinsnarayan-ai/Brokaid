"""Operator queue routes — /api/o/queue and Decision Card actions.

All routes require an MFA-verified operator session. Approval actions
emit `Approval` events (idempotent per envelope+user) and resolve the
queue card. `trust_money` cards go through the two-person rule.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.sessions import Session
from app.kernel import compliance, queue
from app.kernel.trail import append
from app.kernel.two_person import record_approval
from app.middleware.session import require_operator_session
from app.routes._scrub import scrub


router = APIRouter(prefix="/api/o/queue")


class ActionBody(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    edits: dict[str, Any] | None = None
    target_kind: str | None = "workflow"   # matter | workflow (queue = workflow)


def _parse(raw) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


async def _load_proposal(conn, tenant_id: str, proposal_ref: str) -> dict:
    row = await conn.fetchrow(
        "SELECT event_id, ts, type, payload, actor FROM events "
        " WHERE tenant_id = $1::uuid AND event_id = $2::uuid",
        tenant_id, proposal_ref,
    )
    if not row:
        return {}
    return {
        "event_id": str(row["event_id"]),
        "ts": row["ts"].isoformat(timespec="microseconds"),
        "type": row["type"],
        "actor": _parse(row["actor"]),
        "payload": _parse(row["payload"]),
    }


async def _load_evidence(conn, tenant_id: str, refs: list[str]) -> list[dict]:
    if not refs:
        return []
    rows = await conn.fetch(
        "SELECT event_id, ts, type, payload, actor FROM events "
        " WHERE tenant_id = $1::uuid AND event_id = ANY($2::uuid[]) "
        " ORDER BY seq ASC",
        tenant_id, refs,
    )
    return [
        {
            "event_id": str(r["event_id"]),
            "ts": r["ts"].isoformat(timespec="microseconds"),
            "type": r["type"],
            "actor": _parse(r["actor"]),
            "payload": _parse(r["payload"]),
        }
        for r in rows
    ]


@router.get("")
async def list_queue(
    request: Request,
    session: Session = Depends(require_operator_session),
) -> dict:
    pool = request.app.state.pool
    cards = await queue.fetch_pending(pool, session.tenant_id)
    # Enrich with client + matter labels.
    client_ids = list({c["client_id"] for c in cards if c.get("client_id")})
    matter_ids = list({c["matter_id"] for c in cards if c.get("matter_id")})
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", session.tenant_id
            )
            clients = {}
            matters = {}
            if client_ids:
                rows = await c.fetch(
                    "SELECT client_id::text AS client_id, display_name "
                    "  FROM clients WHERE tenant_id = $1::uuid "
                    "   AND client_id = ANY($2::uuid[])",
                    session.tenant_id, client_ids,
                )
                clients = {r["client_id"]: r["display_name"] for r in rows}
            if matter_ids:
                rows = await c.fetch(
                    "SELECT matter_id::text AS matter_id, subject "
                    "  FROM proj_matters WHERE tenant_id = $1::uuid "
                    "   AND matter_id = ANY($2::uuid[])",
                    session.tenant_id, matter_ids,
                )
                matters = {r["matter_id"]: r["subject"] for r in rows}
    for c in cards:
        c["client_display"] = clients.get(c.get("client_id") or "", None)
        c["matter_display"] = matters.get(c.get("matter_id") or "", None)
    return {"cards": cards, "count": len(cards)}


@router.get("/{envelope_id}")
async def get_card(
    envelope_id: str, request: Request,
    session: Session = Depends(require_operator_session),
) -> dict:
    pool = request.app.state.pool
    card = await queue.fetch_card(pool, session.tenant_id, envelope_id)
    if not card:
        raise HTTPException(status_code=404, detail="not_found")

    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", session.tenant_id
            )
            proposal = await _load_proposal(c, session.tenant_id, card["proposal_ref"])
            citation_refs = list((proposal.get("payload") or {}).get("citations", []))
            evidence = await _load_evidence(c, session.tenant_id, citation_refs)

    # Confidence: 4 components + scalar from Confidence object embedded
    # in the AgentOutputProduced payload (never model-reported).
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
            "source": "computed",     # never model-reported
        },
        "mocked_capabilities": (proposal.get("payload") or {}).get(
            "mocked_capabilities", []
        ),
    }


async def _emit_approval(
    pool, *, tenant_id, envelope_id, class_key, user_id, role,
    decision: str, edits=None, reason=None, mocked=False,
) -> str:
    """Emit a shaped Approval event and return event_id.

    Idempotent per (envelope, user, decision). trust_money uses the
    two-person rule module instead.
    """
    ev = await append(
        pool, tenant_id, "Approval",
        actor={"kind": "human", "id": user_id, "name": role},
        payload={
            "envelope_id": envelope_id,
            "class_key": class_key,
            "user_id": user_id,
            "role": role,
            "decision": decision,
            "edits": edits or {},
            "reason": reason,
        },
        idempotency_key=f"appr:{envelope_id}:{user_id}:{decision}",
        mocked=mocked,
    )
    return ev.event_id


@router.post("/{envelope_id}/approve")
async def approve(
    envelope_id: str, request: Request,
    body: ActionBody = Body(default_factory=ActionBody),
    session: Session = Depends(require_operator_session),
) -> dict:
    pool = request.app.state.pool
    card = await queue.fetch_card(pool, session.tenant_id, envelope_id)
    if not card:
        raise HTTPException(status_code=404, detail="not_found")
    if card.get("resolved_at"):
        raise HTTPException(status_code=409, detail="already_resolved")

    class_key = card["class_key"]

    if class_key == "trust_money":
        state = await record_approval(
            pool,
            tenant_id=session.tenant_id, envelope_id=envelope_id,
            class_key=class_key, user_id=session.user_id, role=session.active_role,
            reason=body.reason,
        )
        if state == "satisfied":
            # Fetch the TwoPersonRuleSatisfied event to reference on resolve.
            async with pool.acquire() as c:
                await c.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", session.tenant_id
                )
                ev_row = await c.fetchrow(
                    "SELECT event_id FROM events "
                    " WHERE tenant_id = $1::uuid AND type='TwoPersonRuleSatisfied' "
                    "   AND payload->>'envelope_id' = $2 ORDER BY seq DESC LIMIT 1",
                    session.tenant_id, envelope_id,
                )
            await queue.resolve(
                pool, tenant_id=session.tenant_id, envelope_id=envelope_id,
                resolved_by_event=str(ev_row["event_id"]),
            )
            return {"state": "resolved", "reason": "two_person_rule_satisfied"}
        return {"state": "pending", "reason": "awaiting_second_signer"}

    event_id = await _emit_approval(
        pool, tenant_id=session.tenant_id, envelope_id=envelope_id,
        class_key=class_key, user_id=session.user_id, role=session.active_role,
        decision="approve", reason=body.reason,
    )
    await queue.resolve(
        pool, tenant_id=session.tenant_id, envelope_id=envelope_id,
        resolved_by_event=event_id,
    )
    return {"state": "resolved", "approval_event_id": event_id}


@router.post("/{envelope_id}/edit")
async def edit_and_approve(
    envelope_id: str, request: Request,
    body: ActionBody = Body(default_factory=ActionBody),
    session: Session = Depends(require_operator_session),
) -> dict:
    pool = request.app.state.pool
    card = await queue.fetch_card(pool, session.tenant_id, envelope_id)
    if not card:
        raise HTTPException(status_code=404, detail="not_found")
    if card.get("resolved_at"):
        raise HTTPException(status_code=409, detail="already_resolved")
    event_id = await _emit_approval(
        pool, tenant_id=session.tenant_id, envelope_id=envelope_id,
        class_key=card["class_key"], user_id=session.user_id,
        role=session.active_role, decision="edit",
        edits=body.edits or {}, reason=body.reason,
    )
    await queue.resolve(
        pool, tenant_id=session.tenant_id, envelope_id=envelope_id,
        resolved_by_event=event_id,
    )
    return {"state": "resolved", "approval_event_id": event_id}


@router.post("/{envelope_id}/reject")
async def reject(
    envelope_id: str, request: Request,
    body: ActionBody = Body(default_factory=ActionBody),
    session: Session = Depends(require_operator_session),
) -> dict:
    pool = request.app.state.pool
    card = await queue.fetch_card(pool, session.tenant_id, envelope_id)
    if not card:
        raise HTTPException(status_code=404, detail="not_found")
    if card.get("resolved_at"):
        raise HTTPException(status_code=409, detail="already_resolved")
    event_id = await _emit_approval(
        pool, tenant_id=session.tenant_id, envelope_id=envelope_id,
        class_key=card["class_key"], user_id=session.user_id,
        role=session.active_role, decision="reject", reason=body.reason,
    )
    await queue.resolve(
        pool, tenant_id=session.tenant_id, envelope_id=envelope_id,
        resolved_by_event=event_id,
    )
    return {"state": "resolved", "approval_event_id": event_id}


@router.post("/{envelope_id}/block")
async def block(
    envelope_id: str, request: Request,
    body: ActionBody = Body(default_factory=ActionBody),
    session: Session = Depends(require_operator_session),
) -> dict:
    pool = request.app.state.pool
    card = await queue.fetch_card(pool, session.tenant_id, envelope_id)
    if not card:
        raise HTTPException(status_code=404, detail="not_found")
    if card.get("resolved_at"):
        raise HTTPException(status_code=409, detail="already_resolved")
    if session.active_role != "compliance":
        raise HTTPException(status_code=403, detail="compliance_only")
    block_id = await compliance.add_block(
        pool, tenant_id=session.tenant_id,
        target_kind=body.target_kind or "workflow",
        target_id=envelope_id,
        reason=body.reason or "operator queue block",
        actor={"kind": "human", "id": session.user_id, "name": "compliance"},
    )
    # Look up the ComplianceBlockAdded event to reference on resolve.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", session.tenant_id
            )
            row = await c.fetchrow(
                "SELECT event_id::text AS event_id FROM events "
                " WHERE tenant_id = $1::uuid AND idempotency_key = $2",
                session.tenant_id, f"compl-add:{block_id}",
            )
    ev_id = row["event_id"] if row else None
    await queue.resolve(
        pool, tenant_id=session.tenant_id, envelope_id=envelope_id,
        resolved_by_event=ev_id,
    )
    return {"state": "blocked", "block_id": block_id, "block_event_id": ev_id}
