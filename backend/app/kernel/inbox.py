"""R0.8 — inbox threads, broker replies and reply-by promises.

Threads are keyed by matter_id or envelope_id of client Ask events. Reply bodies live in
`thread_messages`; the ledger carries BrokerReplied (ids, author, time, body hash), PromiseMade
and PromiseKept.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.kernel.trail_mongo import DuplicateIdempotencyKey

SYD = ZoneInfo("Australia/Sydney")
ASK_TYPES = ["ClientQuestionAsked", "ClientMessageReleased", "ClientMessageHITL", "ClientMessageRewriteRequired"]
THREAD_TYPES = ASK_TYPES + ["BrokerReplied", "PromiseMade", "PromiseKept"]
REPLY_ROLES = {"ae", "principal"}
BASIS = "rule: +1 business day"
MAX_BODY = 4000


def _utc(dt):
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def reply_by_for(received: datetime) -> datetime:
    """+1 business day, Mon–Fri, Australia/Sydney wall clock. No public-holiday table."""
    d = _utc(received).astimezone(SYD) + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.astimezone(timezone.utc)


def _clock(d: datetime) -> str:
    return f"{d.hour % 12 or 12}:{d:%M} {'am' if d.hour < 12 else 'pm'}"


def fmt_short(dt: datetime) -> str:
    d = _utc(dt).astimezone(SYD)
    return f"{d:%d/%m/%Y} {_clock(d)}"


def fmt_long(dt: datetime) -> str:
    d = _utc(dt).astimezone(SYD)
    return f"{d:%A} {d.day} {d:%B}, {_clock(d)}"


def status_for(t: dict, now: datetime) -> str:
    if t["answered"]:
        return "Answered"
    rb = t.get("reply_by")
    if rb and now > rb:
        return "Overdue"
    if rb and rb.astimezone(SYD).date() == now.astimezone(SYD).date():
        return "Due today"
    return "Awaiting reply"


async def _emit(store, tenant_id, type_, payload, key, actor=None):
    try:
        return await append_via_dispatch(
            store, tenant_id, type_, idempotency_key=key, payload=payload,
            actor=actor or {"kind": "system", "id": "inbox", "name": "inbox"})
    except DuplicateIdempotencyKey:
        return None


async def record_promise(store, tenant_id: str, thread_id: str, client_id: str, received: datetime) -> None:
    received = _utc(received)
    await _emit(store, tenant_id, "PromiseMade", {
        "thread_id": thread_id, "client_id": client_id, "received_at": received.isoformat(),
        "reply_by": reply_by_for(received).isoformat(), "data_basis": BASIS}, f"promise:{thread_id}")


async def ask(store, tenant_id: str, *, user_id: str, client_id: str, message: str,
              thread_id: str | None = None, received_at: datetime | None = None, test: bool = False) -> str:
    """Seeds/tests: open a thread (ClientQuestionAsked + PromiseMade)."""
    thread_id = thread_id or str(uuid.uuid4())
    received = received_at or datetime.now(timezone.utc)
    payload = {"envelope_id": thread_id, "client_id": client_id, "message": message,
               "received_at": _utc(received).isoformat()}
    if test:
        payload["test"] = True
    await _emit(store, tenant_id, "ClientQuestionAsked", payload, f"ask:in:{thread_id}",
                actor={"kind": "human", "id": user_id, "name": "client_owner"})
    await record_promise(store, tenant_id, thread_id, client_id, received)
    return thread_id


def _thread_key(p: dict) -> str | None:
    return p.get("thread_id") or p.get("matter_id") or p.get("envelope_id")


async def load_threads(store, tenant_id: str, *, client_id: str | None = None,
                       include_tests: bool = False) -> list[dict]:
    safe = wrap(store)
    q: dict = {"type": {"$in": THREAD_TYPES}}
    if client_id:
        q["payload.client_id"] = client_id
    threads: dict[str, dict] = {}
    async with use_tenant(tenant_id):
        names = {c["client_id"]: c["display_name"] async for c in safe.clients.find({})}
        evs = [e async for e in safe.events.find(q).sort("seq", 1)]
        reply_ids = [e["payload"]["reply_id"] for e in evs if e["type"] == "BrokerReplied"]
        bodies = {m["reply_id"]: m async for m in safe.thread_messages.find({"reply_id": {"$in": reply_ids}})}
    for e in evs:
        p, ts = e.get("payload") or {}, _utc(e["ts"])
        tid = _thread_key(p)
        if not tid:
            continue
        t = threads.setdefault(tid, {
            "thread_id": tid, "client_id": p.get("client_id"), "client": names.get(p.get("client_id"), "Unknown client"),
            "subject": None, "messages": [], "reply_by": None, "kept": None, "test": False, "answered": False})
        typ = e["type"]
        if typ == "ClientQuestionAsked":
            at = _utc(p.get("received_at") or ts)
            t["subject"] = t["subject"] or p.get("message")
            t["test"] = t["test"] or bool(p.get("test"))
            t["messages"].append({"who": "client", "text": p.get("message"), "at": at})
            t["answered"] = False
        elif typ == "ClientMessageReleased":
            t["messages"].append({"who": "broker", "text": p.get("message"), "at": ts, "author": "Your broker"})
            t["answered"] = True
        elif typ in ("ClientMessageHITL", "ClientMessageRewriteRequired"):
            t["messages"].append({"who": "pending", "text": "Your broker is preparing a reply.", "at": ts})
        elif typ == "BrokerReplied":
            m = bodies.get(p["reply_id"]) or {}
            t["messages"].append({"who": "broker", "text": m.get("body", ""), "at": _utc(p["replied_at"]),
                                  "author": m.get("author_name") or "Your broker", "reply_id": p["reply_id"]})
            t["answered"] = True
        elif typ == "PromiseMade":
            t["reply_by"] = _utc(p["reply_by"])
        elif typ == "PromiseKept":
            t["kept"] = {"kept_at": _utc(p["kept_at"]), "late": bool(p.get("late"))}
    out = [t for t in threads.values() if t["messages"] and (include_tests or not t["test"])]
    for t in out:
        t["messages"].sort(key=lambda m: m["at"])
        t["last_at"] = t["messages"][-1]["at"]
        t["last_message"] = next((m["text"] for m in reversed(t["messages"]) if m["who"] != "pending"), "")
    return out


def sort_threads(rows: list[dict], now: datetime) -> list[dict]:
    rank = {"Overdue": 0, "Due today": 1, "Awaiting reply": 1, "Answered": 2}
    far = datetime.max.replace(tzinfo=timezone.utc)

    def key(t):
        s = status_for(t, now)
        if s == "Answered":
            return (2, -t["last_at"].timestamp())
        return (rank[s], (t["reply_by"] or far).timestamp())
    return sorted(rows, key=key)


async def get_thread(store, tenant_id: str, thread_id: str) -> dict:
    t = next((t for t in await load_threads(store, tenant_id, include_tests=True)
              if t["thread_id"] == thread_id), None)
    if not t:
        raise HTTPException(status_code=404, detail="thread_not_found")
    return t


async def _audit(store, session, client_id, thread_id, outcome):
    safe = wrap(store)
    async with use_tenant(session.tenant_id):
        await safe.authority_events.insert_one({
            "event_id": str(uuid.uuid4()), "client_id": client_id, "user_id": session.user_id,
            "membership_id": None, "action": "inbox_reply", "outcome": outcome, "policy_id": None,
            "envelope_id": thread_id, "ts": datetime.now(timezone.utc),
            "requested_authority": "can_reply", "actual_authority": {"role": session.active_role}})


async def reply(store, session, thread_id: str, body: str, idempotency_key: str) -> dict:
    tenant_id = session.tenant_id
    t = await get_thread(store, tenant_id, thread_id)
    if session.active_role not in REPLY_ROLES:
        await _audit(store, session, t["client_id"], thread_id, "denied")
        raise HTTPException(status_code=403, detail={
            "code": "AuthorityDenied", "detail": "your role can read threads but not reply", "action": "inbox_reply"})
    safe = wrap(store)
    async with use_tenant(tenant_id):
        prior = await safe.thread_messages.find_one({"idempotency_key": idempotency_key})
        if prior:
            return {"reply_id": prior["reply_id"], "thread_id": prior["thread_id"], "replayed": True}
        user = await safe.users.find_one({"user_id": session.user_id}) or {}
        now = datetime.now(timezone.utc)
        reply_id = str(uuid.uuid4())
        await safe.thread_messages.insert_one({
            "reply_id": reply_id, "thread_id": thread_id, "client_id": t["client_id"], "body": body,
            "author_user_id": session.user_id, "author_name": user.get("display_name") or "Your broker",
            "created_at": now, "idempotency_key": idempotency_key})
    actor = {"kind": "human", "id": session.user_id, "name": session.active_role}
    await _emit(store, tenant_id, "BrokerReplied", {
        "thread_id": thread_id, "client_id": t["client_id"], "reply_id": reply_id,
        "author_user_id": session.user_id, "replied_at": now.isoformat(),
        "body_sha256": hashlib.sha256(body.encode()).hexdigest()}, f"reply:{idempotency_key}", actor)
    late = None
    if t["reply_by"] and not t["kept"]:
        late = now > t["reply_by"]
        await _emit(store, tenant_id, "PromiseKept", {
            "thread_id": thread_id, "client_id": t["client_id"], "reply_id": reply_id,
            "reply_by": t["reply_by"].isoformat(), "kept_at": now.isoformat(), "late": late},
            f"kept:{thread_id}", actor)
    await _audit(store, session, t["client_id"], thread_id, "allowed")
    async with use_tenant(tenant_id):
        await safe.proj_notifications.insert_one({
            "notification_id": str(uuid.uuid4()), "client_id": t["client_id"], "source_event_id": None,
            "ts": now, "kind": "broker_reply", "subject": "Your broker replied",
            "body_text": body[:140], "read_at": None})
    return {"reply_id": reply_id, "thread_id": thread_id, "replayed": False, "late": late}


async def backfill(store, tenant_id: str) -> dict:
    """Re-runnable: PromiseMade for every question thread lacking one; PromiseKept for already-answered ones."""
    made = kept = 0
    for t in await load_threads(store, tenant_id, include_tests=True):
        firsts = [m for m in t["messages"] if m["who"] == "client"]
        if not t["reply_by"] and firsts:
            await record_promise(store, tenant_id, t["thread_id"], t["client_id"], firsts[0]["at"])
            t["reply_by"] = reply_by_for(firsts[0]["at"])
            made += 1
        answer = next((m for m in t["messages"] if m["who"] == "broker"), None)
        if t["reply_by"] and not t["kept"] and answer:
            await _emit(store, tenant_id, "PromiseKept", {
                "thread_id": t["thread_id"], "client_id": t["client_id"], "reply_id": answer.get("reply_id"),
                "reply_by": t["reply_by"].isoformat(), "kept_at": answer["at"].isoformat(),
                "late": answer["at"] > t["reply_by"]}, f"kept:{t['thread_id']}")
            kept += 1
    return {"promises_made": made, "promises_kept": kept}
