"""R0.14 — per-brokerage email settings, EMAIL_ENABLED gate, case-insensitive allow-list."""
import asyncio

import pytest

from app.kernel import email_channel as ec
from tests.test_r12_email import ALLOW, DAY, FakeSend, _alert, _run


@pytest.mark.parametrize("env,enabled,expected", [
    ("production", None, False), ("production", "true", True), ("production", "TRUE", True),
    ("development", None, True), ("development", "false", False), ("preview", "", True),
])
def test_email_enabled_matrix(monkeypatch, env, enabled, expected):
    monkeypatch.setenv("ENVIRONMENT", env)
    if enabled is None:
        monkeypatch.delenv("EMAIL_ENABLED", raising=False)
    else:
        monkeypatch.setenv("EMAIL_ENABLED", enabled)
    assert ec.email_enabled() is expected


def test_allowlist_case_insensitive():
    s = ec.resolve_settings({"email_settings": {"allowlist": [" Luka@UnderAIter.com "]}})
    assert s["allowlist"] == ["luka@underaiter.com"]
    assert ec.allowed("LUKA@underaiter.COM", s["allowlist"]) and not ec.allowed("luka@underaiter.co", s["allowlist"])


def test_tenant_settings_win_env_is_fallback(monkeypatch):
    monkeypatch.setenv("EMAIL_FROM_NAME", "Env Default Name")
    monkeypatch.setenv("EMAIL_ALLOWLIST", "*@env.dev")
    t = {"display_name": "Acme Brokers", "email_settings": {"sender_name": "Acme Insurance", "reply_to": "help@acme.com.au",
                                                            "brand_colour": "#123ABC", "allowlist": []}}
    s = ec.resolve_settings(t)
    assert (s["sender_name"], s["reply_to"], s["brand_colour"], s["allowlist"], s["source"]) == \
        ("Acme Insurance", "help@acme.com.au", "#123ABC", [], "tenant")
    bare = ec.resolve_settings({"display_name": "Acme Brokers"})
    assert bare["sender_name"] == "Acme Brokers" and bare["allowlist"] == ["*@env.dev"] and bare["source"] == "env"
    assert ec.resolve_settings({})["sender_name"] == "Env Default Name"
    assert ec.resolve_settings({"email_settings": {"brand_colour": "red;x"}})["brand_colour"] == ec.DEFAULT_BRAND_COLOUR


def test_render_uses_tenant_name_and_colour_only():
    s = ec.resolve_settings({"display_name": "Acme Brokers", "email_settings": {"brand_colour": "#123ABC",
                                                                              "logo_url": "/brand/acme.png"}})
    subject, html, text = ec.render({"kind": "client_question", "link": "/o/inbox"}, {"display_name": "Cass E"}, s)
    ec._assert_safe_email(subject, html)
    for out in (html, text):
        assert "Acme Brokers" in out and "Harbourline" not in out and "burmac" not in out.lower()
    assert "#123ABC" in html and "#F2B01E" not in html and 'src="https://' in html and "/brand/acme.png" in html
    # SVG logo reference → text header (most mail apps block SVG)
    svg = ec.resolve_settings({"display_name": "Acme Brokers", "email_settings": {"logo_url": "/brand/x.svg"}})
    assert "<img" not in ec.render({"kind": "client_question", "link": "/o"}, {}, svg)[1]


def test_harbourline_record_drives_sender_and_allowlist():
    async def go(db, tid, uid):
        # R1.3: arrange our own tenant record (was relying on preview state set by app/scripts/r14_tenant_email_settings.py)
        await db.tenants.update_one({"tenant_id": tid}, {"$set": {"email_settings": {
            "sender_name": "Harbourline Insurance Brokers", "reply_to": "", "allowlist": ["luka@underaiter.com", *ALLOW]}}})
        s = await ec.tenant_settings(db, tid)
        rec = (await db.tenants.find_one({"tenant_id": tid}))["email_settings"]
        assert s["sender_name"] == rec["sender_name"] and s["source"] == "tenant"
        assert "luka@underaiter.com" in s["allowlist"]
        n = await _alert(db, tid, uid)
        send = FakeSend()
        await ec.run_once(db, DAY, send=send, ids=[n["notification_id"]])  # allow from tenant record
        assert send.calls and send.calls[0]["from_name"] == rec["sender_name"]
        assert "burmac" not in (send.calls[0]["html"] + send.calls[0]["text"]).lower()
    _run(go)


def test_disabled_skips_email_only(monkeypatch):
    async def go(db, tid, uid):
        pending = await _alert(db, tid, uid)
        monkeypatch.setenv("EMAIL_ENABLED", "false")
        n = await _alert(db, tid, uid)
        assert n["channels_state"]["email"] == "skipped:email_disabled"
        assert n["channels_state"]["in_app"] == "delivered" and n["channels_state"]["push"] != "skipped:email_disabled"
        assert n["read_at"] is None
        send = FakeSend()
        await ec.run_once(db, DAY, send=send, allow=ALLOW, ids=[pending["notification_id"]])
        row = await db.notifications.find_one({"_id": pending["_id"]})
        assert row["channels_state"]["email"] == "skipped:email_disabled" and not send.calls
    _run(go)


def test_build_info_reports_email_enabled(monkeypatch):
    from app.routes import build_info
    monkeypatch.setenv("EMAIL_ENABLED", "false")
    assert asyncio.run(build_info.build_info())["email_enabled"] is False



def test_background_sender_skips_test_rows():
    """Runs against a throwaway database: the no-ids path processes every due row it can see."""
    async def go():
        import os
        import uuid as _u
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        name = f"{os.environ['DB_NAME']}_r14_{_u.uuid4().hex[:8]}"
        db = client[name]
        try:
            await db.tenants.insert_one({"tenant_id": "t1", "display_name": "Acme Brokers",
                                         "email_settings": {"allowlist": ["*@resend.dev"]}})
            await db.users.insert_one({"tenant_id": "t1", "user_id": "u1", "kind": "operator", "display_name": "Op",
                                       "email": "op@acme.example", "email_override": "delivered@resend.dev"})
            old = DAY - ec.SETTLE * 3
            for nid, test in (("real", False), ("qa", True)):
                await db.notifications.insert_one({"tenant_id": "t1", "user_id": "u1", "notification_id": nid, "kind": "broker_review",
                                                   "link": "/o/alerts", "test": test, "read_at": None, "updated_at": old,
                                                   "channels_state": {"email": "pending"}})
            send = FakeSend()
            await ec.run_once(db, DAY, send=send)  # background loop: no ids
            state = {n["notification_id"]: n["channels_state"]["email"] async for n in db.notifications.find({})}
            assert state == {"real": "sent", "qa": "pending"} and len(send.calls) == 1
            assert send.calls[0]["from_name"] == "Acme Brokers"
            await ec.run_once(db, DAY, send=send, ids=["qa"])  # explicit test send still works
            assert (await db.notifications.find_one({"notification_id": "qa"}))["channels_state"]["email"] == "sent"
        finally:
            await client.drop_database(name)
    asyncio.run(go())
