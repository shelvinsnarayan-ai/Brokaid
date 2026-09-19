"""Client-facing Ask channel — POST /api/c/ask/message (R5c-4b — Mongo-only)."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.sessions import Session
from app.db.tenant_safe import wrap as _wrap_safe
from app.kernel import claims_scene
from app.kernel.agents.liaison import EmergentLLMProviderLiaison
from app.kernel.release import try_release
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_session


router = APIRouter(prefix="/api/c/ask")


class AskBody(BaseModel):
    message: str


def _store(request: Request):
    s = getattr(request.app.state, "mongo_db", None)
    if s is None:
        raise HTTPException(status_code=503, detail="mongo_store_unavailable")
    return s


@router.post("/message")
async def send_message(
    request: Request,
    body: AskBody = Body(...),
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    store = _store(request)
    tenant_id = session.tenant_id
    client = await claims_scene.load_client_for_user(
        store, tenant_id, session.user_id,
    )
    if not client:
        raise HTTPException(status_code=404, detail="client_not_linked")
    client_name = client["display_name"]
    broker_name = client.get("brokerage_display") or "your broker"
    recent = await claims_scene.recent_client_events_summary(
        store, tenant_id, client["client_id"],
    )

    envelope_id = str(uuid.uuid4())
    await claims_scene.emit_client_question_asked(
        store, tenant_id=tenant_id, user_id=session.user_id,
        envelope_id=envelope_id, client_id=client["client_id"],
        message=body.message,
    )

    liaison = EmergentLLMProviderLiaison()
    draft = await liaison.draft(
        client_message=body.message,
        context={
            "client_display_name": client_name,
            "linked_broker_name": broker_name,
            "matter_kind": "general",
            "recent_events_summary": recent,
            "policy_summary": None,
        },
    )
    release = await try_release(
        store=store,
        tenant_id=tenant_id,
        draft=draft["response_draft"],
        context={
            "matter_kind": "general",
            "client_context_summary": client_name,
            "recent_events_summary": recent,
        },
        author_agent="liaison",
        envelope_id=envelope_id,
        client_id=client["client_id"],
        mocked_channels_used=[],
    )
    return {
        "envelope_id": envelope_id,
        "released": release.released,
        "verdict": release.verdict,
        "draft": {
            "response_draft": draft.get("response_draft"),
            "broker_attribution": draft.get("broker_attribution"),
            "next_step": draft.get("next_step"),
            "detected_coverage_question": draft.get("detected_coverage_question"),
        },
        "classifier_verdict": (
            {
                "advice_classification":
                    release.classifier_verdict.get("advice_classification")
                    if release.classifier_verdict else None,
            }
            if release.classifier_verdict else None
        ),
    }


@router.get("/thread")
async def thread(
    request: Request, session: Session = Depends(require_session),
) -> dict[str, Any]:
    store = _store(request)
    tenant_id = session.tenant_id
    client = await claims_scene.load_client_for_user(
        store, tenant_id, session.user_id,
    )
    if not client:
        raise HTTPException(status_code=404, detail="client_not_linked")
    ask_types = (
        "ClientQuestionAsked", "ClientMessageReleased",
        "ClientMessageHITL", "ClientMessageRewriteRequired",
    )
    safe = _wrap_safe(store)
    rows: list[dict[str, Any]] = []
    async with use_tenant(tenant_id):
        cursor = safe.events.find({
            "type": {"$in": list(ask_types)},
            "payload.client_id": client["client_id"],
        }).sort("seq", 1).limit(40)
        async for r in cursor:
            rows.append({
                "type": r["type"], "ts": r.get("ts"),
                "payload": r.get("payload") or {},
            })

    thread_out = []
    for r in rows:
        p = r["payload"]
        if not isinstance(p, dict):
            p = dict(p) if p else {}
        ts = r["ts"]
        ts_iso = (
            ts.isoformat(timespec="microseconds")
            if hasattr(ts, "isoformat") else str(ts)
        )
        if r["type"] == "ClientQuestionAsked":
            thread_out.append({
                "who": "client", "text": p.get("message"), "ts": ts_iso,
            })
        elif r["type"] == "ClientMessageReleased":
            thread_out.append({
                "who": "broker", "text": p.get("message"),
                "attribution": None, "next_step": None,
                "verified": (
                    (p.get("classifier_verdict") or {}).get("advice_classification")
                    == "factual"
                ),
                "ts": ts_iso,
            })
        else:
            thread_out.append({
                "who": "broker_pending",
                "text": "Your broker is drafting a response. Please keep an eye on this thread.",
                "ts": ts_iso,
            })
    return {"thread": thread_out}
