"""R0.13 Phase C — web push channel: subscribe, quiet-hours hold, 410 cleanup, retries, safe payload."""
import asyncio
import base64
import os
import uuid
from datetime import timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec as crypto_ec

from app.kernel import notify
from app.kernel import push_channel as pc
from tests.test_r12_email import DAY, _alert, syd


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def fake_keys() -> dict:
    pub = crypto_ec.generate_private_key(crypto_ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"p256dh": _b64(pub), "auth": _b64(os.urandom(16))}


class FakeSend:
    def __init__(self, codes):
        self.codes, self.calls = codes, []

    async def __call__(self, sub, data):
        self.calls.append((sub["endpoint"], data))
        return self.codes.get(sub["endpoint"], 201)


def _run(fn):
    async def go():
        from motor.motor_asyncio import AsyncIOMotorClient
        db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        tid = (await db.tenants.find_one({"slug": "harbourline"}))["tenant_id"]
        olly = await db.users.find_one({"tenant_id": tid, "email": "operator@harbourline.example"})
        uid = olly["user_id"]
        await db.notification_prefs.update_one({"tenant_id": tid, "user_id": uid}, {"$set": {"push": True}}, upsert=True)
        eps = [f"https://push.test/{uuid.uuid4()}" for _ in range(2)]
        for e in eps:
            await db.push_subscriptions.insert_one({"tenant_id": tid, "user_id": uid, "endpoint": e, "keys": fake_keys(),
                                                    "user_agent": "pytest", "pytest": True})
        try:
            return await fn(db, tid, uid, eps)
        finally:
            await db.push_subscriptions.delete_many({"pytest": True})
            await db.notification_prefs.delete_many({"tenant_id": tid, "user_id": uid})
    return asyncio.run(go())


def test_channel_registered_and_default_off():
    assert any(isinstance(c, pc.PushChannel) for c in notify.CHANNELS)
    assert asyncio.run(pc.PushChannel().deliver({}, {})) == "skipped:off"
    assert asyncio.run(pc.PushChannel().deliver({}, {"push": True})) == "pending"


def test_payload_is_title_body_link_only():
    p = pc.payload({"kind": "broker_reply", "link": "/c/ask", "notification_id": "n1", "title": "Kai Owner $123"})
    assert set(p) == {"title", "body", "link", "tag"} and p["link"] == "/c/ask"
    assert "$" not in p["title"] + p["body"] and "Kai" not in p["title"] + p["body"]


def test_sent_to_each_device_once_and_410_removes_subscription():
    async def go(db, tid, uid, eps):
        n = await _alert(db, tid, uid)
        assert n["channels_state"]["push"] == "pending"
        send = FakeSend({eps[1]: 410})
        for _ in range(2):
            await pc.run_once(db, DAY, send=send, ids=[n["notification_id"]])
        row = await db.notifications.find_one({"_id": n["_id"]})
        assert row["channels_state"]["push"] == "sent" and len(send.calls) == 2
        assert sorted(r["status"] for r in row["push_results"]) == [201, 410]
        left = [s["endpoint"] async for s in db.push_subscriptions.find({"user_id": uid, "pytest": True})]
        assert left == [eps[0]]
        # all remaining devices gone → skipped:no_device, not retried
        m = await _alert(db, tid, uid)
        await pc.run_once(db, DAY, send=FakeSend({eps[0]: 404}), ids=[m["notification_id"]])
        assert (await db.notifications.find_one({"_id": m["_id"]}))["channels_state"]["push"] == "skipped:no_device"
        assert not await db.push_subscriptions.count_documents({"user_id": uid, "pytest": True})
    _run(go)


def test_quiet_hours_held_then_sent_at_seven_and_read_skips():
    async def go(db, tid, uid, eps):
        n = await _alert(db, tid, uid)
        send = FakeSend({})
        await pc.run_once(db, syd(2036, 10, 16, 22), send=send, ids=[n["notification_id"]])
        assert (await db.notifications.find_one({"_id": n["_id"]}))["channels_state"]["push"] == "held" and not send.calls
        await pc.run_once(db, syd(2036, 10, 17, 6, 30), send=send, ids=[n["notification_id"]])
        assert not send.calls
        await pc.run_once(db, syd(2036, 10, 17, 7, 1), send=send, ids=[n["notification_id"]])
        assert len(send.calls) == 2
        r = await _alert(db, tid, uid)
        await pc.run_once(db, syd(2036, 10, 16, 23), send=send, ids=[r["notification_id"]])
        await db.notifications.update_one({"_id": r["_id"]}, {"$set": {"read_at": DAY}})
        await pc.run_once(db, syd(2036, 10, 17, 7, 1), send=send, ids=[r["notification_id"]])
        assert (await db.notifications.find_one({"_id": r["_id"]}))["channels_state"]["push"] == "skipped:read_in_app"
        assert len(send.calls) == 2
    _run(go)


def test_retries_then_failed():
    async def go(db, tid, uid, eps):
        n = await _alert(db, tid, uid)
        send, t = FakeSend({eps[0]: 500, eps[1]: 503}), DAY
        for _ in range(3):
            await pc.run_once(db, t, send=send, ids=[n["notification_id"]])
            t += timedelta(minutes=11)
        row = await db.notifications.find_one({"_id": n["_id"]})
        assert row["channels_state"]["push"] == "failed" and row["push_attempts"] == 3 and len(send.calls) == 6
        assert await db.push_subscriptions.count_documents({"user_id": uid, "pytest": True}) == 2
    _run(go)


def test_real_webpush_signs_with_configured_vapid_key():
    async def go(db, tid, uid, eps):
        try:
            await pc.webpush_send({"endpoint": "https://127.0.0.1:1/x", "keys": fake_keys()}, {"title": "t"})
        except Exception as e:  # noqa: BLE001 — connection refused is expected; a key/format error is not
            assert "Connection" in type(e).__name__ or "connect" in str(e).lower(), repr(e)
    _run(go)
