"""Operator-side claim scene endpoints (R5c-4b — Mongo-only)."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.sessions import Session
from app.db.tenant_safe import wrap as _wrap_safe
from app.kernel import claims_scene
from app.kernel.notifications import render_and_send
from app.kernel.release import try_release
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_operator_session


router = APIRouter(prefix="/api/o/claims")


class StatusUpdateBody(BaseModel):
    draft: str = Field(..., min_length=1, max_length=4000)


def _store(request: Request):
    s = getattr(request.app.state, "mongo_db", None)
    if s is None:
        raise HTTPException(status_code=503, detail="mongo_store_unavailable")
    return s


def _iso(v):
    return (
        v.isoformat(timespec="microseconds")
        if hasattr(v, "isoformat") else v
    )


@router.get("/{claim_id}")
async def get_claim_operator(
    claim_id: str, request: Request,
    session: Session = Depends(require_operator_session),
) -> dict[str, Any]:
    store = _store(request)
    tenant_id = session.tenant_id
    safe = _wrap_safe(store)
    async with use_tenant(tenant_id):
        claim = await safe.claims.find_one({"claim_id": claim_id})
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
            "client_id": claim.get("client_id"),
        },
        "timeline": timeline,
    }


@router.post("/{claim_id}/status-update")
async def status_update(
    claim_id: str, request: Request,
    body: StatusUpdateBody = Body(...),
    session: Session = Depends(require_operator_session),
) -> dict[str, Any]:
    store = _store(request)
    tenant_id = session.tenant_id
    claim = await claims_scene.load_claim_for_operator(
        store, tenant_id, claim_id,
    )
    if not claim:
        raise HTTPException(status_code=404, detail="claim_not_found")

    envelope_id = str(uuid.uuid4())
    release = await try_release(
        store=store, tenant_id=tenant_id,
        draft=body.draft,
        context={
            "matter_kind": "claim",
            "claim_reference": claim["claim_reference"],
            "client_context_summary": claim["client_id"],
        },
        author_agent="operator",
        matter_id=None,
        envelope_id=envelope_id,
        client_id=claim["client_id"],
        mocked_channels_used=[],
    )

    if release.verdict == "coverage_guard_block":
        return {
            "released": False,
            "verdict": "coverage_guard_block",
            "reason": release.reason,
            "blocked_spans": release.spans,
            "recommended_rewrite": None,
            "guard_visible": True,
        }
    if release.verdict == "rewrite":
        return {
            "released": False,
            "verdict": "rewrite",
            "reason": "compliance classifier requested a rewrite",
            "blocked_spans": [],
            "recommended_rewrite": release.recommended_rewrite,
            "guard_visible": True,
        }
    if release.verdict == "hitl":
        return {
            "released": False,
            "verdict": "hitl",
            "reason": "routed to compliance HITL",
            "guard_visible": True,
        }

    released_ev_id = release.event.event_id if release.event else None
    await claims_scene.append_status_update(
        store, tenant_id,
        claim_id=claim_id, event_id=released_ev_id,
        kind="operator_update", summary=body.draft, mocked=False,
    )
    recipient_email = await claims_scene.load_recipient_email(
        store, tenant_id, claim["client_id"],
    )
    brokerage_display = await claims_scene.load_brokerage_display(
        store, tenant_id,
    )

    notif = await render_and_send(
        store=store, tenant_id=tenant_id, kind="claim_status_update",
        payload={
            "claim_reference": claim["claim_reference"],
            "summary": body.draft,
            "brokerage_display": brokerage_display,
        },
        recipient={
            "client_id": claim["client_id"],
            "email": recipient_email,
        },
        source_event_id=released_ev_id,
    )

    return {
        "released": True,
        "verdict": "release",
        "event_id": released_ev_id,
        "notification": {
            "dev_mode": notif["dev_mode"],
            "subject": notif["subject"],
        },
        "guard_visible": False,
    }


@router.post("/{claim_id}/resolve")
async def resolve_claim(
    claim_id: str, request: Request,
    session: Session = Depends(require_operator_session),
) -> dict[str, Any]:
    store = _store(request)
    tenant_id = session.tenant_id
    claim = await claims_scene.load_claim_for_operator(
        store, tenant_id, claim_id,
    )
    if not claim:
        raise HTTPException(status_code=404, detail="claim_not_found")

    ev = await claims_scene.emit_claim_resolved(
        store, tenant_id=tenant_id, user_id=session.user_id,
        actor_name=session.active_role,
        claim_id=claim_id, claim_reference=claim["claim_reference"],
    )
    await claims_scene.mark_claim_resolved(store, tenant_id, claim_id)
    await claims_scene.append_status_update(
        store, tenant_id,
        claim_id=claim_id, event_id=ev["event_id"],
        kind="resolved", summary="Claim resolved.", mocked=False,
    )
    return {"state": "resolved", "event_id": ev["event_id"]}
