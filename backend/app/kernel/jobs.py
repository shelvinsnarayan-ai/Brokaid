"""R1.1 — durable Mongo job queue: atomic lease claim, heartbeat, exponential backoff, dead-letter,
lease-expiry sweeper, and a single scheduler leader (lease row in `_locks`) that enqueues periodic work
idempotently per time bucket. Jobs are system rows that carry their tenant_id explicitly."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

log = logging.getLogger("brokaid.jobs")
LEASE_S = int(os.environ.get("JOBS_LEASE_S", "30"))
BACKOFF_BASE_S = float(os.environ.get("JOBS_BACKOFF_BASE_S", "5"))
BACKOFF_CAP_S = 600.0
LEADER_TTL_S = 45
TICK_S = 15
HANDLERS: dict[str, Callable[[object, dict], Awaitable[None]]] = {}
LIVE = ("queued", "running")


def handler(kind: str):
    def reg(fn):
        HANDLERS[kind] = fn
        return fn
    return reg


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def worker_id(tag: str = "w") -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{tag}:{uuid.uuid4().hex[:6]}"


def raw(store):
    return getattr(store, "_db", store)


async def ensure_indexes(store) -> None:
    # tenant-safe: system-only — job queue + leader lease are cross-tenant system collections.
    db = raw(store)
    await db.jobs.create_index("idempotency_key", unique=True, name="jobs_idem_uniq")
    await db.jobs.create_index([("status", 1), ("run_after", 1)], name="jobs_claim")
    await db.jobs.create_index([("payload.envelope_id", 1), ("status", 1)], name="jobs_envelope")
    await db["_locks"].create_index("expires_at", expireAfterSeconds=3600, name="locks_ttl")


async def enqueue(store, kind: str, payload: dict, *, key: str, tenant_id: str | None = None,
                  run_after: datetime | None = None, max_attempts: int = 5) -> dict:
    """Idempotent on `key`: re-enqueueing returns the existing job."""
    db, now = raw(store), now_utc()
    doc = {"job_id": str(uuid.uuid4()), "tenant_id": tenant_id, "kind": kind, "payload": payload,
           "idempotency_key": key, "status": "queued", "attempts": 0, "max_attempts": max_attempts,
           "run_after": run_after or now, "lease_owner": None, "lease_expires_at": None, "last_error": None,
           "created_at": now, "updated_at": now, "started_at": None, "finished_at": None}
    try:
        await db.jobs.insert_one(dict(doc))  # tenant-safe: system-only — job queue
        return doc
    except DuplicateKeyError:
        return await db.jobs.find_one({"idempotency_key": key})  # tenant-safe: system-only — job queue


async def claim(store, me: str, now: datetime | None = None) -> dict | None:
    now = now or now_utc()
    # tenant-safe: system-only — atomic lease claim on the job queue.
    return await raw(store).jobs.find_one_and_update(
        {"status": "queued", "run_after": {"$lte": now}},
        {"$set": {"status": "running", "lease_owner": me, "lease_expires_at": now + timedelta(seconds=LEASE_S),
                  "started_at": now, "updated_at": now}, "$inc": {"attempts": 1}},
        sort=[("run_after", 1)], return_document=ReturnDocument.AFTER)


async def heartbeat(store, job: dict, me: str) -> bool:
    now = now_utc()
    r = await raw(store).jobs.update_one(  # tenant-safe: system-only — job queue
        {"job_id": job["job_id"], "lease_owner": me, "status": "running"},
        {"$set": {"lease_expires_at": now + timedelta(seconds=LEASE_S), "updated_at": now}})
    return bool(r.modified_count)


async def save_state(store, job: dict, step: str, value) -> None:
    """Resumable chains record each completed step on the job row."""
    job.setdefault("payload", {}).setdefault("state", {})[step] = value
    await raw(store).jobs.update_one({"job_id": job["job_id"]},  # tenant-safe: system-only — job queue
                                     {"$set": {f"payload.state.{step}": value, "updated_at": now_utc()}})


def backoff_s(attempts: int) -> float:
    return min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** max(0, attempts - 1))


async def _settle(store, job: dict, me: str, error: str | None) -> str:
    now, db = now_utc(), raw(store)
    if error is None:
        status, extra = "succeeded", {"finished_at": now, "last_error": None}
    elif job["attempts"] >= job["max_attempts"]:
        status, extra = "dead", {"finished_at": now, "last_error": error[:500]}
    else:
        status, extra = "queued", {"run_after": now + timedelta(seconds=backoff_s(job["attempts"])), "last_error": error[:500]}
    await db.jobs.update_one({"job_id": job["job_id"], "lease_owner": me},  # tenant-safe: system-only — job queue
                             {"$set": {"status": status, "lease_owner": None, "lease_expires_at": None,
                                       "updated_at": now, **extra}})
    return status


async def run_one(store, me: str) -> bool:
    job = await claim(store, me)
    if not job:
        return False
    fn = HANDLERS.get(job["kind"])

    async def beat():
        while True:
            await asyncio.sleep(max(1, LEASE_S / 3))
            await heartbeat(store, job, me)
    hb = asyncio.create_task(beat())
    err = None
    try:
        if fn is None:
            raise RuntimeError(f"no handler for job kind {job['kind']}")
        await fn(store, job)
    except asyncio.CancelledError:
        hb.cancel()
        raise
    except Exception as e:  # noqa: BLE001 — every failure is recorded on the job
        err = f"{type(e).__name__}: {e}"
        log.warning("job %s (%s) attempt %s failed: %s", job["job_id"], job["kind"], job["attempts"], err)
    finally:
        hb.cancel()
    await _settle(store, job, me, err)
    return True


async def drain(store, me: str = "drain", limit: int = 200) -> int:
    """Run due jobs inline until none are due (tests, scripts)."""
    n = 0
    while n < limit and await run_one(store, me):
        n += 1
    return n


async def sweep(store, now: datetime | None = None) -> dict:
    """Re-queue running jobs whose lease expired (worker died); dead-letter those out of attempts."""
    now, db = now or now_utc(), raw(store)
    expired = {"status": "running", "lease_expires_at": {"$lt": now}}
    gone = "lease expired (worker stopped)"
    # tenant-safe: system-only — job queue maintenance.
    dead = await db.jobs.update_many({**expired, "$expr": {"$gte": ["$attempts", "$max_attempts"]}},
                                     {"$set": {"status": "dead", "lease_owner": None, "finished_at": now,
                                               "last_error": gone, "updated_at": now}})
    back = await db.jobs.update_many(expired, {"$set": {"status": "queued", "lease_owner": None, "run_after": now,
                                                        "last_error": gone, "updated_at": now}})
    return {"requeued": back.modified_count, "dead": dead.modified_count}


async def worker_loop(store, me: str) -> None:
    while True:
        try:
            if not await run_one(store, me):
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("worker %s loop error: %r", me, e)
            await asyncio.sleep(2)


# ---- scheduler (single leader) ----
async def acquire_leader(store, me: str, now: datetime | None = None) -> bool:
    now, db = now or now_utc(), raw(store)
    try:
        # tenant-safe: system-only — leader lease row.
        r = await db["_locks"].find_one_and_update(
            {"_id": "scheduler", "$or": [{"expires_at": {"$lt": now}}, {"owner": me}]},
            {"$set": {"owner": me, "expires_at": now + timedelta(seconds=LEADER_TTL_S), "renewed_at": now}},
            upsert=True, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        return False
    return bool(r) and r["owner"] == me


def _bucket(now: datetime, seconds: int) -> str:
    return str(int(now.timestamp()) // seconds)


PERIODIC = (("notify.periodic", 300, True), ("renewals.tick", 900, True), ("compliance.tick", 900, True), ("backup.nightly", 86400, False), ("backup.drill", 7 * 86400, False), ("email.send", 60, False), ("push.send", 60, False), ("jobs.sweep", 60, False))


async def scheduler_tick(store, me: str, now: datetime | None = None) -> int:
    now = now or now_utc()
    if not await acquire_leader(store, me, now):
        return 0
    n = 0
    # tenant-safe: system-only — tenant ids for per-tenant periodic jobs.
    tenants = [t["tenant_id"] async for t in raw(store).tenants.find({}, {"tenant_id": 1})]
    for kind, every, per_tenant in PERIODIC:
        for tid in (tenants if per_tenant else [None]):
            key = f"{kind}:{tid or 'all'}:{_bucket(now, every)}"
            j = await enqueue(store, kind, {"tenant_id": tid}, key=key, tenant_id=tid, max_attempts=3)
            n += j.get("attempts", 0) == 0 and j.get("status") == "queued"
    return n


async def scheduler_loop(store, me: str) -> None:
    while True:
        try:
            await scheduler_tick(store, me)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("scheduler tick failed: %r", e)
        await asyncio.sleep(TICK_S)


async def start_in_process(store, workers: int = 1) -> list[asyncio.Task]:
    """API process: one sweep now, N workers, and a scheduler candidate."""
    from app.kernel import job_handlers  # noqa: F401 — registers handlers
    await ensure_indexes(store)
    await sweep(store)
    tag = worker_id("api")
    tasks = [asyncio.create_task(worker_loop(store, f"{tag}:{i}")) for i in range(workers)]
    tasks.append(asyncio.create_task(scheduler_loop(store, tag)))
    return tasks


async def summary(store, tenant_id: str) -> dict:
    db, now = raw(store), now_utc()
    scope = {"tenant_id": {"$in": [tenant_id, None]}}
    counts = {s: await db.jobs.count_documents({**scope, "status": s})  # tenant-safe: system-only — job queue, scoped below
              for s in ("queued", "running", "failed", "dead", "succeeded")}
    oldest = await db.jobs.find_one({**scope, "status": "queued", "run_after": {"$lte": now}}, sort=[("run_after", 1)])
    lead = await db["_locks"].find_one({"_id": "scheduler"})
    dead = [j async for j in db.jobs.find({**scope, "status": "dead"}).sort("finished_at", -1).limit(50)]
    return {"counts": counts,
            "oldest_queued_s": round((now - _aware(oldest["run_after"])).total_seconds()) if oldest else 0,
            "leader": {"owner": lead.get("owner"), "alive": _aware(lead["expires_at"]) > now} if lead else {"owner": None, "alive": False},
            "dead": [{"job_id": j["job_id"], "label": LABELS.get(j["kind"], "Background task"), "attempts": j["attempts"], "last_error": j.get("last_error"),
                      "finished_at": _aware(j["finished_at"]).isoformat() if j.get("finished_at") else None,
                      "envelope_id": (j.get("payload") or {}).get("envelope_id")} for j in dead]}


LABELS = {"intake.chain": "Client change request", "notify.event": "Alert", "notify.periodic": "Reminder checks",
          "email.send": "Email sending", "push.send": "Push sending", "jobs.sweep": "Recovery sweep",
          "selftest.poison": "Self-test", "selftest.count": "Self-test"}


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
