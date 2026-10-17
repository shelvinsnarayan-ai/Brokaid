"""R1.0 — release check: step runner, lock, synthetic data isolation, roles, forced fail (in-process + live API)."""
import asyncio
import os
import uuid
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values

from app.kernel import release_check as rc

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values(ROOT / "frontend/.env").get("REACT_APP_BACKEND_URL")


def test_step_runner_statuses_and_inject():
    async def go():
        r = rc.Run("x", inject="b")

        async def ok():
            return "fine"

        async def warn():
            return ("warn", "careful")

        async def boom():
            raise ValueError("kaput")

        await r.step("a", "A", ok)
        with pytest.raises(rc.StepFailed):
            await r.step("b", "B", ok)  # injected
        await r.step("c", "C", warn, soft=True)
        await r.step("d", "D", boom, soft=True)
        return r.checks
    a, b, c, d = asyncio.run(go())
    assert (a["status"], a["detail"]) == ("pass", "fine")
    assert b["status"] == "fail" and "Injected failure" in b["detail"]
    assert c["status"] == "warn" and d["status"] == "fail" and "ValueError: kaput" in d["detail"]
    assert all(isinstance(x["ms"], int) for x in (a, b, c, d))


def _db():
    from motor.motor_asyncio import AsyncIOMotorClient
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"] + f"_rc_{uuid.uuid4().hex[:6]}"]


def test_lock_one_at_a_time_and_stale_takeover():
    async def go():
        db = _db()
        try:
            assert await rc.acquire_lock(db, "r1")
            assert not await rc.acquire_lock(db, "r2")
            await rc.release_lock(db, "r2")  # someone else's release does nothing
            assert not await rc.acquire_lock(db, "r3")
            await db.release_check_lock.update_one({"_id": "lock"}, {"$set": {"started_at": rc.datetime(2000, 1, 1, tzinfo=rc.timezone.utc)}})
            assert await rc.acquire_lock(db, "r4")  # stale lock taken over
            await rc.release_lock(db, "r4")
            assert await rc.acquire_lock(db, "r5")
        finally:
            await db.client.drop_database(db.name)
    asyncio.run(go())


def test_synthetic_data_is_test_flagged_and_invalid_addresses():
    for email, _n, _k in rc.USERS.values():
        assert email.endswith(".invalid")
    from app.kernel import email_channel as ec
    assert ec.resolve_address({"email": rc.USERS["owner"][0]}, ["*"])[0] is None


def test_synthetic_client_seeded_once_in_harbourline_only():
    async def go():
        from motor.motor_asyncio import AsyncIOMotorClient
        db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        h = await db.tenants.find_one({"slug": "harbourline"})
        b = await db.tenants.find_one({"slug": "burmac"})
        ids1 = await rc.ensure_synthetic(db, h["tenant_id"])
        ids2 = await rc.ensure_synthetic(db, h["tenant_id"])
        assert ids1 == ids2
        c = await db.clients.find({"client_id": rc.CLIENT_ID}).to_list(5)
        assert len(c) == 1 and c[0]["tenant_id"] == h["tenant_id"] and c[0]["test"] is True
        assert b is None or not await db.clients.find_one({"tenant_id": b["tenant_id"], "client_id": rc.CLIENT_ID})
        assert await db.users.count_documents({"email": {"$regex": "@releasecheck.invalid$"}}) == 3
    asyncio.run(go())


live = pytest.mark.skipif(not BASE, reason="live API not configured")


def _as(email):
    c = httpx.Client(base_url=BASE, timeout=60)
    assert c.post("/api/auth/dev/impersonate", json={"email": email}).status_code == 200
    return c


@live
def test_roles_and_synthetic_hidden_from_normal_views():
    olly, cass, ada, kai = (_as(e) for e in ("operator@harbourline.example", "ae@harbourline.example",
                                              "principal@harbourline.example", "owner@fictionalclient.example"))
    for c in (cass, ada, kai):
        assert c.post("/api/o/release-check", json={}).status_code == 403
        assert c.get("/api/o/release-check/latest").status_code == 403
    latest = olly.get("/api/o/release-check/latest").json()
    assert "history" in latest and len(latest["history"]) <= 10
    for path in ("/api/o/clients", "/api/o/queue", "/api/o/renewals", "/api/o/inbox"):
        assert "Release Check" not in cass.get(path).text, path
    assert "Release Check" not in ada.get("/api/o/weekly-pack").text
    for c in (cass, ada, kai):
        assert "Release Check" not in c.get("/api/notifications", params={"limit": 50}).text


def test_synthetic_policies_excluded_from_renewals_alerts_and_counts():
    """Two runs' worth of synthetic policies (even near expiry) never alert or count."""
    async def go():
        from datetime import datetime, timedelta, timezone

        from motor.motor_asyncio import AsyncIOMotorClient

        from app.db.tenant_safe import wrap
        from app.kernel import kpis_mongo, notify, registry
        from app.kernel.tenant_ctx import use_tenant
        db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        h = await db.tenants.find_one({"slug": "harbourline"})
        tid, safe = h["tenant_id"], wrap(db)
        await rc.ensure_synthetic(db, tid)
        async with use_tenant(tid):
            before_book = (await kpis_mongo.kpi_1_book(safe, tid)).meta["managed_count"]
        before_reg = await registry.counts(db, tid)
        pids = [await rc.fresh_policy(db, tid, f"pytest-{i}") for i in range(2)]
        try:
            async with use_tenant(tid):
                await safe.policies.update_many({"policy_id": {"$in": pids}},
                                                {"$set": {"expiry": datetime.now(timezone.utc) + timedelta(days=10)}})
            for _ in range(2):
                await notify.run_checks(db, tid)
            async with use_tenant(tid):
                assert not await safe.notifications.count_documents({"dedupe_key": {"$in": [f"renewal_30:{p}" for p in pids]}})
                assert (await kpis_mongo.kpi_1_book(safe, tid)).meta["managed_count"] == before_book
            after_reg = await registry.counts(db, tid)
            assert after_reg["ledger_events"]["value"] == before_reg["ledger_events"]["value"]
        finally:
            async with use_tenant(tid):
                await safe.policies.update_many({"policy_id": {"$in": pids}}, {"$set": {"expiry": datetime.now(timezone.utc) + timedelta(days=335)}})
    asyncio.run(go())
