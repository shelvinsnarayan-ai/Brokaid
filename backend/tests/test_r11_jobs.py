"""R1.1 — durable job queue: lease claim, backoff, dead-letter, sweeper, leader, two worker processes."""
import asyncio
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.kernel import job_handlers, jobs  # noqa: F401

BACKEND = Path(__file__).resolve().parents[1]


def _tmp():
    from motor.motor_asyncio import AsyncIOMotorClient
    name = f"{os.environ['DB_NAME']}_jobs_{uuid.uuid4().hex[:8]}"
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[name], name


def run(fn):
    async def go():
        db, _ = _tmp()
        await jobs.ensure_indexes(db)
        try:
            return await fn(db)
        finally:
            await db.client.drop_database(db.name)
    return asyncio.run(go())


def test_enqueue_is_idempotent_and_claim_is_exclusive():
    async def go(db):
        a = await jobs.enqueue(db, "x", {}, key="k1")
        b = await jobs.enqueue(db, "x", {}, key="k1")
        assert a["job_id"] == b["job_id"] and await db.jobs.count_documents({}) == 1
        got = await asyncio.gather(*[jobs.claim(db, f"w{i}") for i in range(8)])
        assert sum(1 for g in got if g) == 1
    run(go)


def test_backoff_then_dead_then_retry_succeeds():
    async def go(db):
        calls = []

        @jobs.handler("t.flaky")
        async def flaky(store, job):
            calls.append(job["attempts"])
            raise RuntimeError("boom")
        await jobs.enqueue(db, "t.flaky", {}, key="f", max_attempts=3)
        for _ in range(3):
            assert await jobs.run_one(db, "w")
            j = await db.jobs.find_one({"idempotency_key": "f"})
            if j["status"] == "queued":
                wait = (jobs._aware(j["run_after"]) - jobs.now_utc()).total_seconds()
                assert wait > jobs.backoff_s(j["attempts"]) - 2  # exponential delay applied
                await db.jobs.update_one({"_id": j["_id"]}, {"$set": {"run_after": jobs.now_utc()}})
        j = await db.jobs.find_one({"idempotency_key": "f"})
        assert j["status"] == "dead" and calls == [1, 2, 3] and "boom" in j["last_error"]
        assert jobs.backoff_s(1) < jobs.backoff_s(2) < jobs.backoff_s(3)
        # poison self-test: dead until fixed, then a retry succeeds
        await jobs.enqueue(db, "selftest.poison", {"flag": "fix1"}, key="p", max_attempts=1)
        await jobs.run_one(db, "w")
        assert (await db.jobs.find_one({"idempotency_key": "p"}))["status"] == "dead"
        await db.selftest.insert_one({"_id": "fix1"})
        await db.jobs.update_one({"idempotency_key": "p"}, {"$set": {"status": "queued", "attempts": 0}})
        await jobs.run_one(db, "w")
        assert (await db.jobs.find_one({"idempotency_key": "p"}))["status"] == "succeeded"
    run(go)


def test_sweeper_requeues_expired_lease_and_keeps_state():
    async def go(db):
        await jobs.enqueue(db, "t.noop", {"state": {"intake": {"x": 1}}}, key="s")
        j = await jobs.claim(db, "dead-worker")
        await jobs.save_state(db, j, "gates", "ok")
        await db.jobs.update_one({"_id": j["_id"]}, {"$set": {"lease_expires_at": jobs.now_utc() - timedelta(seconds=1)}})
        assert (await jobs.sweep(db))["requeued"] == 1
        j2 = await db.jobs.find_one({"idempotency_key": "s"})
        assert j2["status"] == "queued" and j2["payload"]["state"] == {"intake": {"x": 1}, "gates": "ok"}
        assert not await jobs.heartbeat(db, j, "dead-worker")  # the old owner lost its lease
    run(go)


def test_single_leader_and_idempotent_periodic_buckets():
    async def go(db):
        await db.tenants.insert_one({"tenant_id": "t1", "slug": "t1"})
        # R1.3: injected clock at the start of a 300s bucket (wall clock made now/now+5s straddle a bucket ~8% of runs)
        now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        leaders = await asyncio.gather(*[jobs.acquire_leader(db, f"n{i}", now) for i in range(5)])
        assert sum(leaders) == 1
        winner = f"n{leaders.index(True)}"
        for i in range(5):
            await jobs.scheduler_tick(db, f"n{i}", now)
            await jobs.scheduler_tick(db, winner, now + timedelta(seconds=5))
        kinds = sorted([j["kind"] async for j in db.jobs.find({})])
        assert kinds == ["backup.drill", "backup.nightly", "compliance.tick", "docemail.send", "email.send", "jobs.sweep", "notify.periodic", "push.send",
                         "renewals.tick"]  # once per bucket
        assert await jobs.acquire_leader(db, "late", now + timedelta(seconds=jobs.LEADER_TTL_S + 6))  # takeover after TTL
    run(go)


def test_two_worker_processes_run_each_job_once():
    from motor.motor_asyncio import AsyncIOMotorClient
    name = f"{os.environ['DB_NAME']}_jobs_{uuid.uuid4().hex[:8]}"

    def db():
        return AsyncIOMotorClient(os.environ["MONGO_URL"])[name]

    async def setup():
        d = db()
        await jobs.ensure_indexes(d)
        for i in range(40):
            await jobs.enqueue(d, "selftest.count", {"i": i}, key=f"c{i}")

    async def check():
        d = db()
        rows = [j async for j in d.jobs.find({"kind": "selftest.count"})]
        hits = [h async for h in d.selftest_hits.find({})]
        await d.client.drop_database(name)
        return rows, hits
    asyncio.run(setup())
    procs = [subprocess.Popen([sys.executable, "-m", "app.jobs.worker", "--db", name, "--workers", "2", "--seconds", "12"],
                              cwd=BACKEND) for _ in range(2)]
    for p in procs:
        assert p.wait(timeout=60) == 0
    rows, hits = asyncio.run(check())
    assert len(rows) == 40 and all(r["status"] == "succeeded" for r in rows), [r["status"] for r in rows]
    assert sorted(h["i"] for h in hits) == list(range(40))  # every job ran exactly once
    assert len({h["worker"].split(":")[1] for h in hits}) == 2  # both processes did work


def test_dedupe_links_keeps_newest_valid_and_adds_unique_index():
    from bson import ObjectId

    from app.scripts.r11_dedupe_links import dedupe

    async def go(db):
        base = {"user_id": "u1", "tenant_id": "t1", "client_id": "c1", "role": "client_owner"}
        ids = [ObjectId() for _ in range(4)]
        for oid in ids:
            await db.user_client_links.insert_one({"_id": oid, **base})
        await db.user_client_links.insert_one({**base, "tenant_id": "archived:t0"})  # different key: kept
        await db.memberships.insert_one({**base, "role": "owner", "revoked_at": "2026-01-01"})
        await db.memberships.insert_one({**base, "role": "owner", "revoked_at": None})
        await db.memberships.insert_one({**base, "role": "owner", "revoked_at": "2026-02-01"})
        assert await dedupe(db) == {"user_client_links": 3, "memberships": 2}
        assert (await db.user_client_links.find_one({"tenant_id": "t1"}))["_id"] == ids[-1]  # newest kept
        assert (await db.memberships.find_one({}))["revoked_at"] is None  # valid row kept
        assert await dedupe(db) == {"user_client_links": 0, "memberships": 0}  # re-runnable
        import pymongo.errors
        try:
            await db.user_client_links.insert_one(dict(base))
            raise AssertionError("unique index missing")
        except pymongo.errors.DuplicateKeyError:
            pass
    run(go)


def test_live_links_have_no_duplicates():
    async def go(_db):
        from motor.motor_asyncio import AsyncIOMotorClient
        db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        dup = [g async for g in db.user_client_links.aggregate([
            {"$group": {"_id": {"u": "$user_id", "t": "$tenant_id", "c": "$client_id"}, "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 1}}}])]
        assert dup == []
        assert "ucl_user_tenant_client_uniq" in [i["name"] async for i in db.user_client_links.list_indexes()]
    run(go)
