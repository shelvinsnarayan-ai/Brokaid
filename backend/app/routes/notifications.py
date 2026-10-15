"""R0.11A — /api/notifications: caller-scoped alerts list, read state, prefs (client and broker users)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel import notify
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
              "thread_id": {"$nin": [f["ref"] for f in flags if f["kind"] == "thread"]},
              "reply_id": {"$nin": [f["ref"] for f in flags if f["kind"] == "reply"]}}
    return q


def _view(n: dict) -> dict:
    iso = lambda v: v.replace(tzinfo=v.tzinfo or timezone.utc).isoformat() if v else None  # noqa: E731
    return {"id": n["notification_id"], "kind": n["kind"], "title": n["title"], "body": n["body"],
            "link": n["link"], "count": n.get("count", 1), "created_at": iso(n["created_at"]),
            "updated_at": iso(n.get("updated_at") or n["created_at"]), "read": n["read_at"] is not None,
            "channels_state": n.get("channels_state", {})}


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
    email: bool = False
    push: bool = False


@router.get("/prefs")
async def get_prefs(request: Request, session: Session = Depends(require_session)) -> dict:
    safe = _safe(request)
    async with use_tenant(session.tenant_id):
        p = await notify._prefs(safe, session.user_id)
    return {**p, "in_app_locked": True, "email_available": False, "push_available": False}


@router.put("/prefs")
async def put_prefs(body: PrefsBody, request: Request, session: Session = Depends(require_session)) -> dict:
    safe = _safe(request)
    async with use_tenant(session.tenant_id):
        await safe.notification_prefs.update_one(
            {"user_id": session.user_id},
            {"$set": {"email": body.email, "push": body.push, "updated_at": datetime.now(timezone.utc)}}, upsert=True)
    return await get_prefs(request, session)


@router.post("/run-checks")
async def run_checks(request: Request, session: Session = Depends(require_session)) -> dict:
    """Operator-only: run the periodic promise/renewal checks now (they also run every 5 minutes)."""
    if session.active_role != "operator":
        raise HTTPException(status_code=403, detail="operator_role_required")
    return await notify.run_checks(request.app.state.mongo_db, session.tenant_id)
