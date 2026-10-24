"""R3.9: /api/me/push. Device check status, test send, feedback, and renaming or removing a device. Staff only; clients get 403."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel import push_test as pt
from app.kernel.rbac import ALL_OPERATOR_ROLES
from app.kernel.renewals import _event
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_session

router = APIRouter(prefix="/api/me/push")
STAFF = ALL_OPERATOR_ROLES | {"platform_admin"}


def _staff(s: Session = Depends(require_session)) -> Session:
    if s.active_role not in STAFF:
        raise HTTPException(status_code=403, detail={"code": "staff_only", "detail": "Push tests are for staff accounts."})
    return s


def _dev(s: dict, endpoint: str | None) -> dict:
    iso = lambda v: v.replace(tzinfo=v.tzinfo or timezone.utc).isoformat() if v else None  # noqa: E731
    return {"device_id": s["device_id"], "label": s["label"], "created_at": iso(s.get("created_at")), "last_ok_at": iso(s.get("last_ok_at")),
            "this_device": bool(endpoint) and s["endpoint"] == endpoint,
            "key_current": s.get("vapid_key") in (None, os.environ.get("VAPID_PUBLIC_KEY"))}


@router.get("/status")
async def push_status(request: Request, endpoint: str | None = None, s: Session = Depends(_staff)) -> dict:
    """What the device-check card needs: config, prefs, the caller's devices (marking this one), tests left this hour."""
    store, safe, now = request.app.state.mongo_db, wrap(request.app.state.mongo_db), datetime.now(timezone.utc)
    on, reason = await pt.enabled(store, s.tenant_id)
    async with use_tenant(s.tenant_id):
        devs = [_dev(d, endpoint) for d in await pt.ensure_ids(safe, s.user_id)]
        prefs = await safe.notification_prefs.find_one({"user_id": s.user_id}) or {}
        used = await pt.used_this_hour(safe, s.user_id, now)
    return {"enabled": on, "disabled_reason": reason, "disabled_message": pt.OFF_MSG.get(reason or ""), "prefs_push": bool(prefs.get("push")),
            "public_key": os.environ.get("VAPID_PUBLIC_KEY"), "devices": devs, "this_device": next((d for d in devs if d["this_device"]), None),
            "tests_left": max(0, pt.LIMIT_PER_HOUR - used)}


class TestBody(BaseModel):
    scope: Literal["this_device", "all"]
    subscription_endpoint: str | None = Field(default=None, max_length=2000)


@router.post("/test")
async def push_test_send(body: TestBody, request: Request, s: Session = Depends(_staff)) -> dict:
    """Send the test now through the web push sender, to the caller's own subscriptions only. Quiet hours don't apply."""
    store, safe, now = request.app.state.mongo_db, wrap(request.app.state.mongo_db), datetime.now(timezone.utc)
    on, reason = await pt.enabled(store, s.tenant_id)
    if not on:
        raise HTTPException(status_code=409, detail={"code": "push_disabled", "detail": pt.OFF_MSG[reason]})
    async with use_tenant(s.tenant_id):
        if not (await safe.notification_prefs.find_one({"user_id": s.user_id}) or {}).get("push"):
            raise HTTPException(status_code=409, detail={"code": "push_off", "detail": "Phone and browser alerts are off in your settings. Turn on alerts for this device first."})
        if await pt.used_this_hour(safe, s.user_id, now) >= pt.LIMIT_PER_HOUR:
            raise HTTPException(status_code=429, detail={"code": "push_test_limit", "detail": "You've sent 5 test alerts in the last hour. Try again a little later."},
                                headers={"Retry-After": "600"})
        subs = await pt.ensure_ids(safe, s.user_id)
        if body.scope == "this_device":
            subs = [x for x in subs if body.subscription_endpoint and x["endpoint"] == body.subscription_endpoint]
        if not subs:
            raise HTTPException(status_code=404, detail={"code": "no_device", "detail": "This device isn't set up for alerts yet. Turn on alerts for this device."})
        test_id = str(uuid.uuid4())
        await safe.push_tests.insert_one({"test_id": test_id, "user_id": s.user_id, "scope": body.scope, "created_at": now, "feedback": None})
        results = await pt.send(safe, subs, now=now)
        await safe.push_tests.update_one({"test_id": test_id}, {"$set": {"results": results}})
    await _event(store, s.tenant_id, "PushTestSent", {"test_id": test_id, "user_id": s.user_id, "scope": body.scope,
                 "devices": [{"label": r["label"], "result": r["result"], "status_code": r["status_code"]} for r in results]},
                 f"push_test:{test_id}", s.user_id, s.active_role)
    return {"test_id": test_id, "results": results, "tests_left": max(0, pt.LIMIT_PER_HOUR - await _used(safe, s, now))}


async def _used(safe, s: Session, now: datetime) -> int:
    async with use_tenant(s.tenant_id):
        return await pt.used_this_hour(safe, s.user_id, now)


class FeedbackBody(BaseModel):
    arrived: bool


@router.post("/test/{test_id}/feedback")
async def push_test_feedback(test_id: str, body: FeedbackBody, request: Request, s: Session = Depends(_staff)) -> dict:
    safe = wrap(request.app.state.mongo_db)
    async with use_tenant(s.tenant_id):
        r = await safe.push_tests.update_one({"test_id": test_id, "user_id": s.user_id}, {"$set": {"feedback": body.arrived}})
    if not r.matched_count:
        raise HTTPException(status_code=404, detail="test_not_found")
    await _event(request.app.state.mongo_db, s.tenant_id, "PushTestFeedback", {"test_id": test_id, "user_id": s.user_id, "arrived": body.arrived},
                 f"push_test_feedback:{test_id}:{body.arrived}", s.user_id, s.active_role)
    return {"test_id": test_id, "arrived": body.arrived}


class RenameBody(BaseModel):
    label: str = Field(min_length=1, max_length=60)


@router.patch("/devices/{device_id}")
async def rename_device(device_id: str, body: RenameBody, request: Request, s: Session = Depends(_staff)) -> dict:
    safe, label = wrap(request.app.state.mongo_db), " ".join(body.label.split())
    if not label:
        raise HTTPException(status_code=422, detail="label_required")
    async with use_tenant(s.tenant_id):
        old = await safe.push_subscriptions.find_one({"user_id": s.user_id, "device_id": device_id})
        if not old:
            raise HTTPException(status_code=404, detail="device_not_found")
        await safe.push_subscriptions.update_one({"user_id": s.user_id, "device_id": device_id}, {"$set": {"label": label}})
    await _event(request.app.state.mongo_db, s.tenant_id, "PushDeviceRenamed", {"user_id": s.user_id, "device_id": device_id, "old": old.get("label"),
                 "new": label}, f"push_device_rename:{device_id}:{uuid.uuid4()}", s.user_id, s.active_role)
    return {"device_id": device_id, "label": label}


@router.delete("/devices/{device_id}")
async def remove_device(device_id: str, request: Request, s: Session = Depends(_staff)) -> dict:
    safe = wrap(request.app.state.mongo_db)
    async with use_tenant(s.tenant_id):
        old = await safe.push_subscriptions.find_one({"user_id": s.user_id, "device_id": device_id})
        if not old:
            raise HTTPException(status_code=404, detail="device_not_found")
        await safe.push_subscriptions.delete_one({"user_id": s.user_id, "device_id": device_id})
        n = await safe.push_subscriptions.count_documents({"user_id": s.user_id})
        if not n:
            await safe.notification_prefs.update_one({"user_id": s.user_id}, {"$set": {"push": False}}, upsert=True)
    await _event(request.app.state.mongo_db, s.tenant_id, "PushDeviceRemoved", {"user_id": s.user_id, "device_id": device_id, "label": old.get("label")},
                 f"push_device_remove:{device_id}", s.user_id, s.active_role)
    return {"removed": True, "devices": n}
