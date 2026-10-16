"""R0.12 Phase B — email channel: allow-list, .example skip, quiet hours, read-before-send, toggle, retries."""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dotenv import load_dotenv

from app.kernel import email_channel as ec
from app.kernel import inbox, notify

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / "backend/.env")
ALLOW = ["*@resend.dev"]


def syd(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=inbox.SYD).astimezone(timezone.utc)


def test_quiet_hours_and_next_send():
    assert ec.in_quiet_hours(syd(2026, 10, 16, 22)) and ec.in_quiet_hours(syd(2026, 10, 17, 6, 59))
    assert not ec.in_quiet_hours(syd(2026, 10, 16, 7)) and not ec.in_quiet_hours(syd(2026, 10, 16, 20, 59))
    assert ec.next_send_time(syd(2026, 10, 16, 22)) == syd(2026, 10, 17, 7)
    assert ec.next_send_time(syd(2026, 10, 17, 3)) == syd(2026, 10, 17, 7)


def test_address_rules():
    assert ec.resolve_address({"email": "ae@harbourline.example"}, ALLOW) == (None, "skipped:example_address")
    assert ec.resolve_address({"email": "x@example.test"}, ALLOW)[1] == "skipped:example_address"
    assert ec.resolve_address({"email": "a@harbourline.example", "email_override": "delivered@resend.dev"}, ALLOW) == ("delivered@resend.dev", None)
    assert ec.resolve_address({"email": "a@b.example", "email_override": "someone@gmail.com"}, ALLOW)[1] == "skipped:not_allowlisted"
    assert ec.allowed("Owner@Synaion.com.au", ["*@synaion.com.au"]) and not ec.allowed("o@notsynaion.com.au", ["*@synaion.com.au"])


def test_render_is_plain_and_safe():
    n = {"kind": "broker_reply", "link": "/c/ask", "title": "Your broker replied"}
    subject, html, text = ec.render(n, {"display_name": "Kai Owner"}, ec.resolve_settings({"display_name": "Harbourline Insurance Brokers"}))
    ec._assert_safe_email(subject, html)
    assert "Hi Kai," in text and "Owner" not in text and "Turn these emails off in You → Notifications." in html
    assert html.count("<a ") == 1 and os.environ["APP_BASE_URL"] + "/c/ask" in html and "$" not in html


class FakeSend:
    def __init__(self, fail=0):
        self.calls, self.fail = [], fail

    async def __call__(self, **kw):
        self.calls.append(kw)
        if len(self.calls) <= self.fail:
            raise RuntimeError("boom")
        return f"msg-{len(self.calls)}"


def _run(fn):
    async def go():
        from motor.motor_asyncio import AsyncIOMotorClient
        db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        tid = (await db.tenants.find_one({"slug": "harbourline"}))["tenant_id"]
        olly = await db.users.find_one({"tenant_id": tid, "email": "operator@harbourline.example"})
        await db.users.update_one({"_id": olly["_id"]}, {"$set": {"email_override": "delivered@resend.dev"}})
        await db.notification_prefs.delete_many({"tenant_id": tid, "user_id": olly["user_id"]})
        try:
            return await fn(db, tid, olly["user_id"])
        finally:  # leave the operator's demo routing as it was
            await db.users.update_one({"_id": olly["_id"]}, {"$set": {"email_override": olly.get("email_override")}})
    return asyncio.run(go())


async def _alert(db, tid, uid, key=None, **kw):
    key = key or f"pytest-email:{uuid.uuid4()}"
    await notify.deliver(db, tid, uid, kind="broker_review", title="T", many="T ({n})", body="B", link="/o/alerts",
                         dedupe_key=key, idem=f"{key}:{uuid.uuid4()}", test=True, **kw)
    return await db.notifications.find_one({"dedupe_key": key, "user_id": uid}, sort=[("created_at", -1)])


DAY = syd(2026, 10, 19, 10) + timedelta(days=3650)  # well after creation, business hours


def test_sent_once_for_coalesced_row_and_skips():
    async def go(db, tid, uid):
        key = f"pytest-email:{uuid.uuid4()}"
        n = await _alert(db, tid, uid, key)
        await _alert(db, tid, uid, key)  # coalesces into the same row
        assert (await db.notifications.find_one({"_id": n["_id"]}))["count"] == 2
        send = FakeSend()
        for _ in range(2):
            await ec.run_once(db, DAY, send=send, allow=ALLOW, ids=[n["notification_id"]])
        row = await db.notifications.find_one({"_id": n["_id"]})
        assert len(send.calls) == 1 and row["channels_state"]["email"] == "sent" and row["email_message_id"] == "msg-1"
        assert send.calls[0]["to"] == "delivered@resend.dev"
        # read in-app before send → skipped
        r = await _alert(db, tid, uid)
        await db.notifications.update_one({"_id": r["_id"]}, {"$set": {"read_at": DAY}})
        await ec.run_once(db, DAY, send=send, allow=ALLOW, ids=[r["notification_id"]])
        assert (await db.notifications.find_one({"_id": r["_id"]}))["channels_state"]["email"] == "skipped:read_in_app"
        # toggle off → created as skipped:off, never sent
        await db.notification_prefs.update_one({"tenant_id": tid, "user_id": uid}, {"$set": {"email": False}}, upsert=True)
        o = await _alert(db, tid, uid)
        assert o["channels_state"]["email"] == "skipped:off"
        await db.notification_prefs.delete_many({"tenant_id": tid, "user_id": uid})
        assert len(send.calls) == 1
    _run(go)


def test_quiet_hours_held_then_sent_at_seven():
    async def go(db, tid, uid):
        n = await _alert(db, tid, uid)
        night = syd(2036, 10, 16, 22)
        send = FakeSend()
        await ec.run_once(db, night, send=send, allow=ALLOW, ids=[n["notification_id"]])
        row = await db.notifications.find_one({"_id": n["_id"]})
        assert row["channels_state"]["email"] == "held" and not send.calls
        await ec.run_once(db, syd(2036, 10, 17, 6, 30), send=send, allow=ALLOW, ids=[n["notification_id"]])
        assert not send.calls
        await ec.run_once(db, syd(2036, 10, 17, 7, 1), send=send, allow=ALLOW, ids=[n["notification_id"]])
        assert len(send.calls) == 1
        assert (await db.notifications.find_one({"_id": n["_id"]}))["channels_state"]["email"] == "sent"
    _run(go)


def test_example_address_skipped_and_retries_then_failed():
    async def go(db, tid, uid):
        await db.users.update_one({"user_id": uid}, {"$set": {"email_override": None}})
        n = await _alert(db, tid, uid)
        await ec.run_once(db, DAY, send=FakeSend(), allow=ALLOW, ids=[n["notification_id"]])
        assert (await db.notifications.find_one({"_id": n["_id"]}))["channels_state"]["email"] == "skipped:example_address"
        await db.users.update_one({"user_id": uid}, {"$set": {"email_override": "delivered@resend.dev"}})
        f = await _alert(db, tid, uid)
        send, t = FakeSend(fail=99), DAY
        for _ in range(3):
            await ec.run_once(db, t, send=send, allow=ALLOW, ids=[f["notification_id"]])
            t += timedelta(minutes=11)
        row = await db.notifications.find_one({"_id": f["_id"]})
        assert len(send.calls) == 3 and row["channels_state"]["email"] == "failed" and row["email_attempts"] == 3
        assert row["read_at"] is None  # in-app row untouched
    _run(go)


@pytest.mark.parametrize("kind", ["client", "operator"])
def test_default_email_pref_by_kind(kind):
    async def go(db, tid, _uid):
        u = await db.users.find_one({"tenant_id": tid, "kind": kind,
                                     "email": {"$in": ["owner@fictionalclient.example", "ae@harbourline.example"]}})
        from app.db.tenant_safe import wrap
        from app.kernel.tenant_ctx import use_tenant
        async with use_tenant(tid):
            row = await wrap(db).notification_prefs.find_one({"user_id": u["user_id"]})
            p = await notify._prefs(wrap(db), u["user_id"])
        if not row or "email" not in row:
            assert p["email"] is (kind == "operator")
    _run(go)
