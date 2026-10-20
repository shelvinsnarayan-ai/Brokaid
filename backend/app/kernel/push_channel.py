"""R0.13 Phase C — standard Web Push (VAPID) channel + sender loop. Mirrors the email channel rules:
prefs toggle, quiet hours (hold → 07:00), read-before-send skip, one push per row, retries.
404/410 from a push service removes that device's subscription.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from app.kernel import email_channel as ec

log = logging.getLogger("brokaid.push")
SEND_EVERY_S = 60
OK = {200, 201, 202}
GONE = {404, 410}


class PushChannel:
    name = "push"

    async def deliver(self, n: dict, prefs: dict) -> str:
        return "pending" if prefs.get("push") else "skipped:off"


def configured() -> bool:
    return bool(os.environ.get("VAPID_PUBLIC_KEY") and os.environ.get("VAPID_PRIVATE_KEY"))


def payload(n: dict) -> dict:
    """Same fixed copy as email: no client names, premiums or policy numbers."""
    title, body = ec.COPY.get(n["kind"], ("You have a new alert", "There is something new for you."))
    return {"title": title, "body": body, "link": n["link"], "tag": n["notification_id"]}


async def webpush_send(sub: dict, data: dict) -> int:
    from pywebpush import WebPushException, webpush

    def go() -> int:
        try:
            r = webpush(subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
                        data=json.dumps(data), vapid_private_key=os.environ["VAPID_PRIVATE_KEY"],
                        vapid_claims={"sub": os.environ["VAPID_SUBJECT"]}, ttl=3600, timeout=15)
            return r.status_code
        except WebPushException as e:
            if e.response is not None:
                return e.response.status_code
            raise
    return await asyncio.to_thread(go)


async def run_once(store, now: datetime | None = None, *, send=None, ids: list[str] | None = None,
                   ignore_quiet: bool = False) -> dict:
    # tenant-safe: system-only — the sender spans tenants; every row/subscription is matched on its tenant_id.
    now, send = now or datetime.now(timezone.utc), send or webpush_send
    q: dict = {"channels_state.push": {"$in": ["pending", "held", "retry"]},
               "$or": [{"push_next_at": None}, {"push_next_at": {"$lte": now}}, {"push_next_at": {"$exists": False}}]}
    if ids:
        q["notification_id"] = {"$in": ids}
    else:  # background loop never sends test rows; explicit test sends pass ids
        q["updated_at"] = {"$lte": now - ec.SETTLE}
        q["test"] = {"$ne": True}
    from app.kernel.platform import hold_suspended
    q["tenant_id"] = {"$nin": await hold_suspended(store, "notifications", "channels_state.push")}  # R2.6
    out: dict = {}
    async for n in store.notifications.find(q).limit(200):
        s = await _process(store, n, now, send, ignore_quiet)
        out[s] = out.get(s, 0) + 1
    return out


async def _set(store, n, status, **extra) -> str:
    await store.notifications.update_one({"_id": n["_id"]}, {"$set": {"channels_state.push": status, **extra}})
    return status


async def _process(store, n, now, send, ignore_quiet) -> str:
    if n.get("read_at"):
        return await _set(store, n, "skipped:read_in_app")
    if (await store.users.find_one({"tenant_id": n["tenant_id"], "user_id": n["user_id"]}) or {}).get("disabled"):
        return await _set(store, n, "skipped:user_disabled")
    prefs = await store.notification_prefs.find_one({"tenant_id": n["tenant_id"], "user_id": n["user_id"]}) or {}
    if not prefs.get("push"):
        return await _set(store, n, "skipped:off")
    if ec.in_quiet_hours(now) and not ignore_quiet:
        return await _set(store, n, "held", push_next_at=ec.next_send_time(now))
    subs = [s async for s in store.push_subscriptions.find({"tenant_id": n["tenant_id"], "user_id": n["user_id"]})]
    if not subs:
        return await _set(store, n, "skipped:no_device")
    claimed = await store.notifications.update_one(
        {"_id": n["_id"], "channels_state.push": n["channels_state"]["push"]}, {"$set": {"channels_state.push": "sending"}})
    if not claimed.modified_count:
        return "raced"
    data, results, ok, gone = payload(n), [], 0, 0
    for s in subs:
        try:
            code = await send(s, data)
        except Exception as e:  # noqa: BLE001
            log.warning("push send error: %r", e)
            code = 0
        results.append({"device": s.get("user_agent", "")[:60], "status": code})
        if code in OK:
            ok += 1
            await store.push_subscriptions.update_one({"_id": s["_id"]}, {"$set": {"last_ok_at": now}})
        elif code in GONE:
            gone += 1
            await store.push_subscriptions.delete_one({"_id": s["_id"]})
    attempts = n.get("push_attempts", 0) + 1
    if ok:
        return await _set(store, n, "sent", push_sent_at=now, push_results=results, push_attempts=attempts)
    if gone == len(subs):
        return await _set(store, n, "skipped:no_device", push_results=results)
    if attempts >= ec.MAX_ATTEMPTS:
        return await _set(store, n, "failed", push_results=results, push_attempts=attempts)
    return await _set(store, n, "retry", push_results=results, push_attempts=attempts,
                      push_next_at=now + ec.BACKOFF[attempts - 1])


async def loop(store) -> None:
    await store.push_subscriptions.create_index([("tenant_id", 1), ("user_id", 1), ("endpoint", 1)], unique=True)
    while True:
        try:
            if configured():
                await run_once(store)
        except Exception as e:  # noqa: BLE001
            log.warning("push sender failed (non-fatal): %r", e)
        await asyncio.sleep(SEND_EVERY_S)
