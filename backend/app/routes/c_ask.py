"""Client-facing Ask channel — POST /api/c/ask/message (R5c-4b — Mongo-only)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.sessions import Session
from app.db.tenant_safe import wrap as _wrap_safe
from app.kernel import claims_scene, inbox
from app.kernel.agents.liaison import EmergentLLMProviderLiaison
from app.kernel.release import try_release
from app.kernel.tenant_ctx import use_tenant
from app.kernel.authority import require_client_authority
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
    await require_client_authority(store, session, action="message_attempt", needed="can_view")
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
    await inbox.record_promise(store, tenant_id, envelope_id, client["client_id"], datetime.now(timezone.utc))

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
    request: Request, include_tests: bool = False, session: Session = Depends(require_session),
) -> dict[str, Any]:
    """Client view of every Ask thread, in time order. Open threads show the reply-by promise."""
    store = _store(request)
    client = await claims_scene.load_client_for_user(store, session.tenant_id, session.user_id)
    if not client:
        raise HTTPException(status_code=404, detail="client_not_linked")
    threads = await inbox.load_threads(store, session.tenant_id, client_id=client["client_id"],
                                       include_tests=include_tests)
    items: list[tuple] = []
    waiting = []
    for t in threads:
        for m in t["messages"]:
            if m["who"] == "client":
                items.append((m["at"], {"who": "client", "text": m["text"], "thread_id": t["thread_id"]}))
            elif m["who"] == "broker":
                items.append((m["at"], {"who": "broker", "text": m["text"], "attribution": m.get("author"),
                                        "next_step": None, "verified": False, "thread_id": t["thread_id"],
                                        "reply_id": m.get("reply_id"), "gaw": m.get("gaw")}))
        if not t["answered"] and t["reply_by"]:
            waiting.append(t["reply_by"])
            items.append((t["last_at"], {"who": "broker_pending", "thread_id": t["thread_id"],
                                         "text": f"Your broker will reply by {inbox.fmt_long(t['reply_by'])}."}))
    items.sort(key=lambda x: x[0])
    out = [{**d, "ts": at.isoformat()} for at, d in items]
    return {"thread": out, "reply_by_text": inbox.fmt_long(min(waiting)) if waiting else None}


# ---- R0.9 — unread broker replies (UI state only: no ledger events for "seen") ----
class SeenBody(BaseModel):
    thread_id: str


async def _client_reader(request: Request, session: Session) -> tuple[Any, dict]:
    from app.kernel.rbac import ALL_OPERATOR_ROLES
    if session.active_role in ALL_OPERATOR_ROLES:
        raise HTTPException(status_code=403, detail="client_only")
    store = _store(request)
    client = await claims_scene.load_client_for_user(store, session.tenant_id, session.user_id)
    if not client:
        raise HTTPException(status_code=404, detail="client_not_linked")
    return store, client


@router.get("/unread")
async def unread(request: Request, include_tests: bool = False,
                 session: Session = Depends(require_session)) -> dict[str, Any]:
    store, client = await _client_reader(request, session)
    threads = await inbox.load_threads(store, session.tenant_id, client_id=client["client_id"],
                                       include_tests=include_tests)
    safe = _wrap_safe(store)
    async with use_tenant(session.tenant_id):
        seen = {s["thread_id"]: inbox._utc(s["last_seen_at"])
                async for s in safe.ask_seen.find({"user_id": session.user_id})}
    out = []
    for t in threads:
        since = seen.get(t["thread_id"])
        ids = [m["reply_id"] for m in t["messages"] if m.get("reply_id") and (since is None or m["at"] > since)]
        if ids:
            out.append({"thread_id": t["thread_id"], "unread": len(ids), "reply_ids": ids})
    return {"count": sum(t["unread"] for t in out), "threads": out}


@router.post("/seen")
async def seen(body: SeenBody, request: Request, session: Session = Depends(require_session)) -> dict[str, Any]:
    store, client = await _client_reader(request, session)
    threads = await inbox.load_threads(store, session.tenant_id, client_id=client["client_id"], include_tests=True)
    if not any(t["thread_id"] == body.thread_id for t in threads):
        raise HTTPException(status_code=404, detail="thread_not_found")
    now = datetime.now(timezone.utc)
    safe = _wrap_safe(store)
    async with use_tenant(session.tenant_id):
        await safe.ask_seen.update_one(
            {"user_id": session.user_id, "thread_id": body.thread_id},
            {"$max": {"last_seen_at": now}, "$setOnInsert": {"client_id": client["client_id"]}}, upsert=True)
    return {"thread_id": body.thread_id, "seen": True}
