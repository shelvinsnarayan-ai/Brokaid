"""R3.9: staff push test. The user sends a test alert to their own devices right now: no queue, no quiet hours.
Each device gets a result. 404/410 removes the subscription. 5 tests per user per hour. Ledgered as PushTestSent and PushTestFeedback."""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from app.kernel import push_channel

LIMIT_PER_HOUR = 5
SEND_TIMEOUT_S = 10
TITLE = "brokAId test alert ✓"
LINK = "/o/alerts#settings"
OFF_MSG = {
    "not_configured": "Phone alerts aren't set up on this server yet (no push keys). Ask your operator.",
    "env_off": "Phone alerts are switched off for this brokerage. Ask your operator.",
    "tenant_off": "Phone alerts are switched off for this brokerage. Ask your operator.",
}


def label_from_ua(ua: str, standalone: bool = False) -> str:
    ua = ua or ""
    os_ = ("iPhone" if "iPhone" in ua else "iPad" if "iPad" in ua else "Android" if "Android" in ua else "ChromeOS" if "CrOS" in ua
           else "Mac" if "Macintosh" in ua or "Mac OS X" in ua else "Windows" if "Windows" in ua else "Linux" if "Linux" in ua else "this device")
    br = ("Edge" if "Edg/" in ua else "Samsung Internet" if "SamsungBrowser" in ua else "Firefox" if re.search(r"Firefox|FxiOS", ua)
          else "Chrome" if re.search(r"Chrome|CriOS", ua) else "Safari" if "Safari" in ua or os_ in ("iPhone", "iPad") else "Browser")
    return f"{br} on {os_}" + (" (Home Screen app)" if standalone else "")


async def enabled(store, tid: str) -> tuple[bool, str | None]:
    """Push on for this environment and brokerage? Returns (on, reason) where reason is a key of OFF_MSG."""
    if not push_channel.configured():
        return False, "not_configured"
    if os.environ.get("PUSH_DISABLED") == "1":
        return False, "env_off"
    t = await store.tenants.find_one({"tenant_id": tid}, {"push_disabled": 1}) or {}  # tenant-safe: system-only — one tenant row by id
    return (False, "tenant_off") if t.get("push_disabled") else (True, None)


def _result(code: int | None, sub: dict) -> dict:
    out = {"device_id": sub["device_id"], "label": sub.get("label") or label_from_ua(sub.get("user_agent", "")), "status_code": code}
    if code in push_channel.OK:
        return out | {"result": "delivered", "message": "Delivered to the push service."}
    if code in push_channel.GONE:
        return out | {"result": "expired", "message": "This device's alert setup had expired. Turn alerts on again."}
    if code == 403:
        return out | {"result": "error", "message": "The push service refused this device's alert keys. Turn alerts on again on this device."}
    if code == 429:
        return out | {"result": "error", "message": "The push service is busy. Try again in a few minutes."}
    if code is None:
        return out | {"result": "error", "message": "The push service didn't answer within 10 seconds. Try again in a minute."}
    if not code:
        return out | {"result": "error", "message": "Couldn't reach this device's push service. Try again in a minute."}
    return out | {"result": "error", "message": f"The push service didn't accept the alert (code {code})."}


async def send(safe, subs: list[dict], *, now: datetime) -> list[dict]:
    """Send to each of the caller's subscriptions in parallel, with a 10 s limit. Expired subscriptions are removed."""
    async def one(s: dict) -> dict:
        data = {"title": TITLE, "body": f"If you can see this, alerts work on {s.get('label') or label_from_ua(s.get('user_agent', ''))}.",
                "link": LINK, "tag": f"push-test-{s['device_id']}"}
        try:
            code = await asyncio.wait_for(push_channel.webpush_send(s, data), SEND_TIMEOUT_S)
        except asyncio.TimeoutError:
            code = None
        except Exception:  # noqa: BLE001
            code = 0
        r = _result(code, s)
        if r["result"] == "delivered":
            await safe.push_subscriptions.update_one({"device_id": s["device_id"]}, {"$set": {"last_ok_at": now}})
        elif r["result"] == "expired":
            await safe.push_subscriptions.delete_one({"device_id": s["device_id"]})
        return r
    return list(await asyncio.gather(*(one(s) for s in subs)))


async def ensure_ids(safe, user_id: str) -> list[dict]:
    """The caller's subscriptions. Older rows get a device_id and a label from their user agent."""
    out = []
    async for s in safe.push_subscriptions.find({"user_id": user_id}):
        if not s.get("device_id") or not s.get("label"):
            s["device_id"] = s.get("device_id") or str(uuid.uuid4())
            s["label"] = s.get("label") or label_from_ua(s.get("user_agent", ""))
            await safe.push_subscriptions.update_one({"_id": s["_id"]}, {"$set": {"device_id": s["device_id"], "label": s["label"]}})
        out.append(s)
    return sorted(out, key=lambda s: s.get("created_at") or datetime.min.replace(tzinfo=timezone.utc))


async def used_this_hour(safe, user_id: str, now: datetime) -> int:
    return await safe.push_tests.count_documents({"user_id": user_id, "created_at": {"$gt": now - timedelta(hours=1)}})
