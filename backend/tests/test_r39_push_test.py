"""R3.9 staff push test. Sends only to the caller's own subscriptions, 410 removes the subscription, 5 per hour then 429,
clients get 403, quiet hours are ignored, disabled-by-config gives 409 with a plain message, devices can be renamed and removed (ledgered)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.db.tenant_safe import wrap
from app.kernel import email_channel, push_channel, push_test
from app.kernel.tenant_ctx import use_tenant
from tests.test_r34_claim_threads import _staff_cookie
from tests.test_r35_claim_attachments import BLOBS, _setup, fake_storage  # noqa: F401


async def _sub(db, tid, user_id, endpoint, **kw):
    async with use_tenant(tid):
        await wrap(db).push_subscriptions.insert_one({"user_id": user_id, "endpoint": endpoint, "keys": {"p256dh": "x", "auth": "y"},
                                                      "user_agent": kw.get("ua", "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) Chrome/131.0 Safari/537.36"),
                                                      "created_at": datetime.now(timezone.utc), **{k: v for k, v in kw.items() if k != "ua"}})


def test_labels():
    assert push_test.label_from_ua("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit Chrome/131.0 Safari/537.36") == "Chrome on Mac"
    ios = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"
    assert push_test.label_from_ua(ios, True) == "Safari on iPhone (Home Screen app)"
    assert push_test.label_from_ua("Mozilla/5.0 (Windows NT 10.0) Chrome/131.0 Safari/537.36 Edg/131.0") == "Edge on Windows"


@pytest.mark.asyncio
async def test_push_test(app_client, mongo_db, monkeypatch):
    _, http = app_client
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:t@example.com")
    monkeypatch.delenv("PUSH_DISABLED", raising=False)
    monkeypatch.setattr(email_channel, "in_quiet_hours", lambda now: True)  # quiet hours ignored for tests
    tid, _o, _c, _f, ids, cookies, _other = await _setup(mongo_db)
    ae = await _staff_cookie(mongo_db, tid, ids["ae"], "ae")
    other = str(uuid.uuid4())
    sent: list[str] = []

    async def fake_send(sub, data):
        sent.append(sub["endpoint"])
        assert data["title"] == "brokAId test alert ✓" and data["link"] == "/o/alerts#settings" and "alerts work on" in data["body"]
        return 410 if "gone" in sub["endpoint"] else 201
    monkeypatch.setattr(push_channel, "webpush_send", fake_send)
    await _sub(mongo_db, tid, ids["ae"], "https://push.example/mine")
    await _sub(mongo_db, tid, ids["ae"], "https://push.example/gone", ua="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari/604.1")
    await _sub(mongo_db, tid, other, "https://push.example/someone-else")
    async with use_tenant(tid):
        await wrap(mongo_db).notification_prefs.update_one({"user_id": ids["ae"]}, {"$set": {"push": True}}, upsert=True)

    # clients: 403, no card data
    assert (await http.get("/api/me/push/status", headers=cookies["owner"])).status_code == 403
    assert (await http.post("/api/me/push/test", headers=cookies["owner"], json={"scope": "all"})).status_code == 403

    st = (await http.get("/api/me/push/status", params={"endpoint": "https://push.example/mine"}, headers=ae)).json()
    assert st["enabled"] and st["prefs_push"] and len(st["devices"]) == 2 and st["this_device"]["label"] == "Chrome on Mac"
    # this device only → just mine, even during quiet hours
    r = await http.post("/api/me/push/test", headers=ae, json={"scope": "this_device", "subscription_endpoint": "https://push.example/mine"})
    assert r.status_code == 200, r.text
    assert sent == ["https://push.example/mine"] and r.json()["results"][0]["result"] == "delivered"
    # someone else's endpoint as "this device" → nothing sent to them
    r = await http.post("/api/me/push/test", headers=ae, json={"scope": "this_device", "subscription_endpoint": "https://push.example/someone-else"})
    assert r.status_code == 404 and "https://push.example/someone-else" not in sent
    # all → own two only; the 410 one is removed with the plain message
    sent.clear()
    r = (await http.post("/api/me/push/test", headers=ae, json={"scope": "all"})).json()
    assert sorted(sent) == ["https://push.example/gone", "https://push.example/mine"]
    gone = next(x for x in r["results"] if x["result"] == "expired")
    assert gone["message"] == "This device's alert setup had expired. Turn alerts on again."
    async with use_tenant(tid):
        assert not await wrap(mongo_db).push_subscriptions.find_one({"endpoint": "https://push.example/gone"})
        assert await wrap(mongo_db).push_subscriptions.find_one({"endpoint": "https://push.example/someone-else"})
        ev = await wrap(mongo_db).events.find_one({"type": "PushTestSent", "payload.test_id": r["test_id"]})
    assert ev and {d["result"] for d in ev["payload"]["devices"]} == {"delivered", "expired"}
    # feedback ledgered
    assert (await http.post(f"/api/me/push/test/{r['test_id']}/feedback", headers=ae, json={"arrived": False})).status_code == 200
    async with use_tenant(tid):
        assert await wrap(mongo_db).events.find_one({"type": "PushTestFeedback", "payload.test_id": r["test_id"], "payload.arrived": False})

    # rate limit: 5 sends per hour counted (2 above + the 404 didn't count) → 3 more then 429
    for _ in range(3):
        assert (await http.post("/api/me/push/test", headers=ae, json={"scope": "all"})).status_code == 200
    r = await http.post("/api/me/push/test", headers=ae, json={"scope": "all"})
    assert r.status_code == 429 and "5 test alerts" in r.json()["detail"]["detail"]

    # rename / remove (ledgered); can't touch another user's device
    dev = (await http.get("/api/me/push/status", headers=ae)).json()["devices"][0]
    assert (await http.patch(f"/api/me/push/devices/{dev['device_id']}", headers=ae, json={"label": "Work laptop"})).json()["label"] == "Work laptop"
    async with use_tenant(tid):
        theirs = await wrap(mongo_db).push_subscriptions.find_one({"user_id": other})
    assert (await http.delete(f"/api/me/push/devices/{theirs.get('device_id') or 'x'}", headers=ae)).status_code == 404
    assert (await http.delete(f"/api/me/push/devices/{dev['device_id']}", headers=ae)).json() == {"removed": True, "devices": 0}
    async with use_tenant(tid):
        assert await wrap(mongo_db).events.find_one({"type": "PushDeviceRenamed", "payload.new": "Work laptop"})
        assert await wrap(mongo_db).events.find_one({"type": "PushDeviceRemoved", "payload.device_id": dev["device_id"]})
        assert not (await wrap(mongo_db).notification_prefs.find_one({"user_id": ids["ae"]}))["push"]


@pytest.mark.asyncio
async def test_disabled_by_config(app_client, mongo_db, monkeypatch):
    _, http = app_client
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "priv")
    tid, _o, _c, _f, ids, _cookies, _other = await _setup(mongo_db)
    pr = await _staff_cookie(mongo_db, tid, ids["principal"], "principal")
    await mongo_db.tenants.update_one({"tenant_id": tid}, {"$set": {"push_disabled": True}}, upsert=True)
    st = (await http.get("/api/me/push/status", headers=pr)).json()
    assert not st["enabled"] and st["disabled_message"] == "Phone alerts are switched off for this brokerage. Ask your operator."
    r = await http.post("/api/me/push/test", headers=pr, json={"scope": "all"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "push_disabled"
    await mongo_db.tenants.update_one({"tenant_id": tid}, {"$unset": {"push_disabled": ""}})
    monkeypatch.setenv("PUSH_DISABLED", "1")
    assert (await http.get("/api/me/push/status", headers=pr)).json()["disabled_reason"] == "env_off"
    monkeypatch.delenv("PUSH_DISABLED")
    monkeypatch.delenv("VAPID_PRIVATE_KEY")
    assert (await http.get("/api/me/push/status", headers=pr)).json()["disabled_reason"] == "not_configured"
