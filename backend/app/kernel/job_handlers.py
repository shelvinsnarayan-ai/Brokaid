"""R1.1 — job kinds. Importing this module registers every handler."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.kernel import email_channel, iadviser, jobs, notify, push_channel


@jobs.handler("notify.event")
async def _notify_event(store, job: dict) -> None:
    p = job["payload"]
    await notify.on_event(store, job["tenant_id"], p["type"], p.get("payload") or {}, p["event_id"])


async def _paused(store, job: dict) -> bool:
    """R2.6: a suspended tenant's periodic work is skipped (a job queued before the suspension does nothing)."""
    from app.kernel.platform import is_suspended
    return await is_suspended(store, job["payload"]["tenant_id"])


@jobs.handler("notify.periodic")
async def _notify_periodic(store, job: dict) -> None:
    if not await _paused(store, job):
        await notify.run_checks(store, job["payload"]["tenant_id"])


@jobs.handler("renewals.tick")
async def _renewals_tick(store, job: dict) -> None:
    from app.kernel import renewals
    if not await _paused(store, job):
        await renewals.tick(store, job["payload"]["tenant_id"])


@jobs.handler("compliance.tick")
async def _compliance_tick(store, job: dict) -> None:
    from app.kernel import claims, complaints
    if await _paused(store, job):
        return
    await complaints.tick(store, job["payload"]["tenant_id"])
    await claims.tick(store, job["payload"]["tenant_id"])


@jobs.handler("privacy.export")
async def _privacy_export(store, job: dict) -> None:
    from app.kernel import privacy_export
    p = job["payload"]
    await privacy_export.build(store, job["tenant_id"], p["matter_id"], p["client_id"], p["user_id"])


@jobs.handler("backup.nightly")
async def _backup_nightly(store, job: dict) -> None:
    from app.kernel import backup
    await backup.backup_all(store)


@jobs.handler("backup.drill")
async def _backup_drill(store, job: dict) -> None:
    from app.kernel import backup
    # tenant-safe: system-only — weekly drill on each tenant's latest backup
    async for t in store.tenants.find({}, {"tenant_id": 1}):
        await backup.drill(store, t["tenant_id"])


@jobs.handler("ai.eval")
async def _ai_eval(store, job: dict) -> None:
    from app.eval.run import run
    await run(store, by=job["payload"].get("by") or "governance")


@jobs.handler("email.send")
async def _email_send(store, job: dict) -> None:
    await email_channel.run_once(store)


@jobs.handler("push.send")
async def _push_send(store, job: dict) -> None:
    await push_channel.run_once(store)


@jobs.handler("docemail.send")
async def _docemail_send(store, job: dict) -> None:
    from app.kernel import doc_email
    await doc_email.run_once(store)


@jobs.handler("intake.chain")
async def _intake(store, job: dict) -> None:
    from app.routes.c_scene import intake_job
    await intake_job(store, job)


@jobs.handler("jobs.sweep")
async def _sweep(store, job: dict) -> None:
    await jobs.sweep(store)
    await recover_stuck_envelopes(store)


@jobs.handler("selftest.poison")
async def _poison(store, job: dict) -> None:
    """Fails until its flag row exists — proves retry/backoff/dead-letter/panel retry."""
    # tenant-safe: system-only — self-test flag row.
    if not await jobs.raw(store).selftest.find_one({"_id": job["payload"]["flag"]}):
        raise RuntimeError("poison job: flag not set (expected while testing)")


@jobs.handler("selftest.count")
async def _count(store, job: dict) -> None:
    """Records which worker ran it — the two-worker test checks every job ran exactly once."""
    # tenant-safe: system-only — self-test rows in a throwaway test database.
    await jobs.raw(store).selftest_hits.insert_one({"i": job["payload"]["i"], "worker": job["lease_owner"]})


TERMINAL = {"EndorsementQuoteReady", "RequestNeedsHumanReview"}


async def recover_stuck_envelopes(store, now: datetime | None = None, window_h: int = 6) -> int:
    """Requests with no queue card, no terminal event and no job at all get an intake job."""
    now, db = now or datetime.now(timezone.utc), jobs.raw(store)
    n = 0
    # tenant-safe: system-only — cross-tenant recovery scan; every enqueue carries the event's tenant_id.
    async for ev in db.events.find({"type": "ClientChangeRequested",
                                    "ts": {"$gte": (now - timedelta(hours=window_h)).isoformat(),
                                           "$lte": (now - timedelta(minutes=2)).isoformat()}}):
        p = ev.get("payload") or {}
        env, tid = p.get("envelope_id"), ev["tenant_id"]
        if not env or not p.get("policy_id") or await db.jobs.find_one({"payload.envelope_id": env, "kind": "intake.chain"}):
            continue
        if await db.events.find_one({"tenant_id": tid, "payload.envelope_id": env, "type": {"$in": list(TERMINAL)}}) \
                or await db.proj_queue.find_one({"tenant_id": tid, "envelope_id": env}):
            continue
        await jobs.enqueue(store, "intake.chain", {"envelope_id": env, "policy_id": p["policy_id"],
                                                   "user_id": (ev.get("actor") or {}).get("id"),
                                                   "raw_message": p.get("raw_message") or "", "state": {}},
                           key=f"intake:{env}", tenant_id=tid, max_attempts=3)
        n += 1
    return n


@jobs.handler("import.commit")
async def _import_commit(store, job: dict) -> None:
    await iadviser.commit_run(store, job["tenant_id"], job["payload"]["run_id"])
