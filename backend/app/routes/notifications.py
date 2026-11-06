"""R0.11A — /api/notifications: caller-scoped alerts list, read state, prefs (client and broker users)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ConfigDict, BaseModel, EmailStr

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel import email_channel, notify, push_channel
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_session

router = APIRouter(prefix="/api/notifications")


def _safe(request: Request):
    return wrap(request.app.state.mongo_db)


async def _scope(safe, session: Session, include_tests: bool) -> dict:
    q: dict[str, Any] = {"user_id": session.user_id}
    if not include_tests:
        flags = [f async for f in safe.test_flags.find({})]
        q |= {"test": {"$ne": True},
              "thread_id": {"$nin": [f["ref"] for f in flags if f["kind"] == "thread"
                                     and not str(f["ref"]).startswith("claim-")]},  # R3.4: claim threads hide per message
              "reply_id": {"$nin": [f["ref"] for f in flags if f["kind"] == "reply"]}}
    return q


SEVERITY = {"promise_overdue": "urgent", "advised_no_receipt": "urgent", "bank_unmatched": "urgent", "client_question": "action",
            "broker_review": "action", "approval_needed": "action", "promise_due": "action", "renewal_30": "action"}


def _view(n: dict) -> dict:
    iso = lambda v: v.replace(tzinfo=v.tzinfo or timezone.utc).isoformat() if v else None  # noqa: E731
    return {"id": n["notification_id"], "kind": n["kind"], "title": n["title"], "body": n["body"],
            "link": n["link"], "count": n.get("count", 1), "created_at": iso(n["created_at"]),
            "updated_at": iso(n.get("updated_at") or n["created_at"]), "read": n["read_at"] is not None,
            "channels_state": n.get("channels_state", {}), "client_name": n.get("client_name"),
            "severity": SEVERITY.get(n["kind"], "fyi")}


@router.get("")
async def list_notifications(request: Request, unread_only: bool = False, limit: int = 50,
                             include_tests: bool = False, session: Session = Depends(require_session)) -> dict:
    safe = _safe(request)
    async with use_tenant(session.tenant_id):
        q = await _scope(safe, session, include_tests)
        if unread_only:
            q["read_at"] = None
        rows = [n async for n in safe.notifications.find(q).sort("updated_at", -1).limit(max(1, min(limit, 200)))]
    return {"notifications": [_view(n) for n in rows]}


@router.get("/unread-count")
async def unread_count(request: Request, include_tests: bool = False,
                       session: Session = Depends(require_session)) -> dict:
    safe = _safe(request)
    async with use_tenant(session.tenant_id):
        q = await _scope(safe, session, include_tests)
        return {"count": await safe.notifications.count_documents({**q, "read_at": None})}


@router.post("/read-all")
async def read_all(request: Request, session: Session = Depends(require_session)) -> dict:
    safe = _safe(request)
    async with use_tenant(session.tenant_id):
        r = await safe.notifications.update_many({"user_id": session.user_id, "read_at": None},
                                                 {"$set": {"read_at": datetime.now(timezone.utc)}})
    return {"marked": r.modified_count}


@router.post("/{notification_id}/read")
async def read_one(notification_id: str, request: Request, session: Session = Depends(require_session)) -> dict:
    safe = _safe(request)
    async with use_tenant(session.tenant_id):
        n = await safe.notifications.find_one({"notification_id": notification_id, "user_id": session.user_id})
        if not n:
            raise HTTPException(status_code=404, detail="notification_not_found")
        if n["read_at"] is None:
            await safe.notifications.update_one({"notification_id": notification_id},
                                                {"$set": {"read_at": datetime.now(timezone.utc)}})
    return {"id": notification_id, "read": True, "link": n["link"]}


class PrefsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")  # R2.6: unknown fields are a 422
    email: bool = False
    push: bool = False  # stored for phase C; push is not sent yet
    documents_email: bool | None = None  # R2.5: "Documents by email" (separate from alert emails)


@router.get("/prefs")
async def get_prefs(request: Request, session: Session = Depends(require_session)) -> dict:
    safe = _safe(request)
    async with use_tenant(session.tenant_id):
        p = await notify._prefs(safe, session.user_id)
    return {**p, "in_app_locked": True, "email_available": email_channel.email_enabled(), "push_available": push_channel.configured()}


@router.put("/prefs")
async def put_prefs(body: PrefsBody, request: Request, session: Session = Depends(require_session)) -> dict:
    safe = _safe(request)
    async with use_tenant(session.tenant_id):
        await safe.notification_prefs.update_one(
            {"user_id": session.user_id},
            {"$set": {"email": body.email, "push": body.push, "updated_at": datetime.now(timezone.utc),
                      **({"documents_email": body.documents_email} if body.documents_email is not None else {})}}, upsert=True)
    return await get_prefs(request, session)


@router.post("/run-checks")
async def run_checks(request: Request, session: Session = Depends(require_session)) -> dict:
    """Operator-only: run the periodic promise/renewal checks now (they also run every 5 minutes)."""
    if session.active_role != "operator":
        raise HTTPException(status_code=403, detail="operator_role_required")
    return await notify.run_checks(request.app.state.mongo_db, session.tenant_id)


class OverrideBody(BaseModel):
    user_id: str
    email_override: EmailStr | None = None


def _require_operator(session: Session) -> None:
    if session.active_role != "operator":
        raise HTTPException(status_code=403, detail="operator_role_required")


@router.get("/email-overrides")
async def list_overrides(request: Request, session: Session = Depends(require_session)) -> dict:
    """Operator-only: demo personas use .example addresses; route their alerts to real inboxes."""
    _require_operator(session)
    safe = _safe(request)
    async with use_tenant(session.tenant_id):
        rows = [u async for u in safe.users.find({}).sort("email", 1)]
    settings = await email_channel.tenant_settings(request.app.state.mongo_db, session.tenant_id)
    return {"users": [{"user_id": u["user_id"], "name": u.get("display_name"), "email": u["email"],
                       "email_override": u.get("email_override"), "kind": u.get("kind")} for u in rows],
            "allowlist": settings["allowlist"]}


@router.get("/email-settings")
async def email_settings(request: Request, session: Session = Depends(require_session)) -> dict:
    """Operator-only, read-only: this brokerage's email branding + allow-list, and whether email is on here."""
    _require_operator(session)
    s = await email_channel.tenant_settings(request.app.state.mongo_db, session.tenant_id)
    return {**s, "email_enabled": email_channel.email_enabled()}


@router.put("/email-overrides")
async def set_override(body: OverrideBody, request: Request, session: Session = Depends(require_session)) -> dict:
    _require_operator(session)
    safe = _safe(request)
    async with use_tenant(session.tenant_id):
        r = await safe.users.update_one({"user_id": body.user_id},
                                        {"$set": {"email_override": str(body.email_override) if body.email_override else None}})
    if not r.matched_count:
        raise HTTPException(status_code=404, detail="user_not_found")
    allow = (await email_channel.tenant_settings(request.app.state.mongo_db, session.tenant_id))["allowlist"]
    return {"user_id": body.user_id, "email_override": body.email_override,
            "allowlisted": bool(body.email_override) and email_channel.allowed(str(body.email_override), allow)}


@router.post("/email-test")
async def email_test(request: Request, session: Session = Depends(require_session)) -> dict:
    """Operator-only: create one in-app alert for the caller and run the email sender for it now."""
    _require_operator(session)
    store = request.app.state.mongo_db
    import uuid
    key = f"email-test:{uuid.uuid4()}"
    await notify.deliver(store, session.tenant_id, session.user_id, kind="broker_review",
                         title="Email test", many="Email test", body="A test alert from Agent operations.",
                         link="/o/alerts", dedupe_key=key, idem=key, test=True)
    async with use_tenant(session.tenant_id):
        n = await _safe(request).notifications.find_one({"idem_keys": key})
    from datetime import timedelta
    res = await email_channel.run_once(store, datetime.now(timezone.utc) + email_channel.SETTLE + timedelta(seconds=1),
                                       ids=[n["notification_id"]], ignore_quiet=True)
    async with use_tenant(session.tenant_id):
        n = await _safe(request).notifications.find_one({"idem_keys": key})
    return {"result": res, "email": n["channels_state"].get("email"), "message_id": n.get("email_message_id")}


# ---- R0.13 Phase C — web push subscriptions (per user, per device) ----
class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSub(BaseModel):
    endpoint: str
    keys: PushKeys
    user_agent: str | None = None
    standalone: bool = False  # R3.9: installed (Home Screen) app, for the device label


class PushUnsub(BaseModel):
    endpoint: str


@router.get("/push-public-key")
async def push_public_key(session: Session = Depends(require_session)) -> dict:
    import os
    return {"key": os.environ.get("VAPID_PUBLIC_KEY"), "available": push_channel.configured()}


@router.post("/push-subscriptions")
async def add_push_subscription(body: PushSub, request: Request, session: Session = Depends(require_session)) -> dict:
    if not body.endpoint.startswith("https://"):
        raise HTTPException(status_code=422, detail="endpoint_must_be_https")
    import os
    import uuid
    from app.kernel import push_test
    safe, now = _safe(request), datetime.now(timezone.utc)
    ua = (body.user_agent or request.headers.get("user-agent", ""))[:200]
    async with use_tenant(session.tenant_id):
        await safe.push_subscriptions.update_one(
            {"user_id": session.user_id, "endpoint": body.endpoint},
            {"$set": {"keys": body.keys.model_dump(), "user_agent": ua, "updated_at": now, "vapid_key": os.environ.get("VAPID_PUBLIC_KEY")},
             "$setOnInsert": {"created_at": now, "last_ok_at": None, "device_id": str(uuid.uuid4()),
                              "label": push_test.label_from_ua(ua, body.standalone)}}, upsert=True)
        await safe.notification_prefs.update_one({"user_id": session.user_id}, {"$set": {"push": True}}, upsert=True)
        n = await safe.push_subscriptions.count_documents({"user_id": session.user_id})
    return {"subscribed": True, "devices": n}


@router.delete("/push-subscriptions")
async def remove_push_subscription(body: PushUnsub, request: Request, session: Session = Depends(require_session)) -> dict:
    safe = _safe(request)
    async with use_tenant(session.tenant_id):
        await safe.push_subscriptions.delete_one({"user_id": session.user_id, "endpoint": body.endpoint})
        n = await safe.push_subscriptions.count_documents({"user_id": session.user_id})
        if not n:
            await safe.notification_prefs.update_one({"user_id": session.user_id}, {"$set": {"push": False}}, upsert=True)
    return {"subscribed": False, "devices": n}


@router.post("/push-test")
async def push_test(request: Request, session: Session = Depends(require_session)) -> dict:
    """Operator-only: send a test push to the caller's own devices now (ignores quiet hours)."""
    _require_operator(session)
    import uuid
    store, key = request.app.state.mongo_db, f"push-test:{uuid.uuid4()}"
    async with use_tenant(session.tenant_id):
        prefs_row = await _safe(request).notification_prefs.find_one({"user_id": session.user_id}) or {}
    if not prefs_row.get("push"):
        return {"push": "skipped:off", "results": [], "hint": "Turn on push on this device first (Alerts → Notification settings)."}
    await notify.deliver(store, session.tenant_id, session.user_id, kind="broker_review", title="Push test",
                         many="Push test", body="A test alert from Agent operations.", link="/o/alerts",
                         dedupe_key=key, idem=key, test=True)
    async with use_tenant(session.tenant_id):
        n = await _safe(request).notifications.find_one({"idem_keys": key})
    await push_channel.run_once(store, ids=[n["notification_id"]], ignore_quiet=True)
    async with use_tenant(session.tenant_id):
        n = await _safe(request).notifications.find_one({"idem_keys": key})
    return {"push": n["channels_state"].get("push"), "results": n.get("push_results", [])}
