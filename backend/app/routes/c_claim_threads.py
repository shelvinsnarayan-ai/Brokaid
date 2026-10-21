"""R3.4 — client claim threads: GET /api/c/claim-threads, GET|POST /api/c/claims/{claim_id}/thread (text only; no AI auto-reply)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel import authority, inbox
from app.kernel import claim_threads as ct
from app.kernel import claims as ck
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_session
from app.routes.c_claims import _client, _mine, _store

router = APIRouter(prefix="/api/c", tags=["client-claim-threads"])


class PostBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(max_length=inbox.MAX_BODY)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message cannot be empty")
        return v.strip()


async def _seen(store, session) -> dict:
    async with use_tenant(session.tenant_id):
        return {s["thread_id"]: inbox._utc(s["last_seen_at"]) async for s in wrap(store).ask_seen.find({"user_id": session.user_id})}


def _unread(t: dict | None, since) -> list[str]:
    return [m["reply_id"] for m in (t or {}).get("messages", []) if m.get("reply_id") and (since is None or m["at"] > since)]


async def _claim(store, session, claim_id: str) -> dict:
    c = await _mine(store, session, claim_id)  # central ownership guard: another client's claim is a 404
    if c.get("test") or "stage" not in c:
        raise HTTPException(status_code=404, detail="claim_not_found")
    return c


@router.get("/claim-threads")
async def list_threads(request: Request, session: Session = Depends(require_session)) -> dict[str, Any]:
    store = _store(request)
    cid = await _client(store, session)
    now = datetime.now(timezone.utc)
    async with use_tenant(session.tenant_id):
        claims = [c async for c in wrap(store).claims.find({"client_id": cid, "test": {"$ne": True}, "stage": {"$exists": True}}, {"_id": 0})]
    threads = {t["claim_id"]: t for t in await inbox.load_threads(store, session.tenant_id, client_id=cid) if t.get("claim_id")}
    seen, names = await _seen(store, session), await ct.vehicle_names(store, session.tenant_id, cid)
    rows = []
    for c in claims:
        t, closed = threads.get(c["claim_id"]), ct.is_closed(c)
        if closed and not t:
            continue
        rows.append({"claim_id": c["claim_id"], "claim_reference": c.get("claim_reference"), "label": ct.label(c, names),
                     "stage_label": ck.row(c, now)["stage_label"], "open": not closed, "thread_id": t["thread_id"] if t else None,
                     "last_message": t["last_message"][:120] if t else None, "last_at": t["last_at"].isoformat() if t else None,
                     "unread": len(_unread(t, seen.get(ct.thread_id_for(c["claim_id"])))),
                     "sort": (t["last_at"] if t else inbox._utc(c.get("lodged_at") or now)).isoformat()})
    rows.sort(key=lambda r: r.pop("sort"), reverse=True)
    rows.sort(key=lambda r: not r["open"])
    return {"threads": rows}


@router.get("/claims/{claim_id}/thread")
async def get_thread(claim_id: str, request: Request, session: Session = Depends(require_session)) -> dict[str, Any]:
    store = _store(request)
    c = await _claim(store, session, claim_id)
    t = await ct.thread_for(store, session.tenant_id, c["client_id"], claim_id)
    since = (await _seen(store, session)).get(ct.thread_id_for(claim_id))
    msgs = [{"who": m["who"], "text": m["text"], "at": m["at"].isoformat(), "author": m.get("author"), "reply_id": m.get("reply_id"),
             "gaw": m.get("gaw")} for m in (t or {}).get("messages", []) if m["who"] in ("client", "broker")]
    closed = ct.is_closed(c)
    waiting = t and not t["answered"] and t["reply_by"]
    return {"claim": {"claim_id": claim_id, "claim_reference": c.get("claim_reference"),
                      "label": ct.label(c, await ct.vehicle_names(store, session.tenant_id, c["client_id"])),
                      "stage_label": ck.row(c, datetime.now(timezone.utc))["stage_label"]},
            "thread_id": ct.thread_id_for(claim_id), "exists": bool(t), "messages": msgs, "unread_reply_ids": _unread(t, since),
            "read_only": closed, "closed_note": ct.CLOSED_NOTE if closed else None,
            "can_post": not closed and await authority.has(store, session, c["client_id"], "can_lodge_claim"),
            "reply_by_text": f"Your broker will reply by {inbox.fmt_long(t['reply_by'])}." if waiting else None}


@router.post("/claims/{claim_id}/thread")
async def post_message(claim_id: str, request: Request, body: PostBody = Body(...),
                       session: Session = Depends(require_session)) -> dict[str, Any]:
    store = _store(request)
    c = await _claim(store, session, claim_id)
    if ct.is_closed(c):
        raise HTTPException(status_code=409, detail={"code": "claim_closed", "detail": ct.CLOSED_NOTE})
    await authority.require_authority(store, session, client_id=c["client_id"], action="claim_message", needed="can_lodge_claim")
    return await ct.post_client(store, session.tenant_id, user_id=session.user_id, role=session.active_role, claim=c, message=body.message)
