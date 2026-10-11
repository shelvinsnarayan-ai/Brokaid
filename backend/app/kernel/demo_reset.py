"""R0.5 — operator demo reset (rehearsal tool). Idempotent; ledger stays append-only.

kai       : policy …104 back to seed schedule; abandon Kai's open envelopes and
            close his open queue cards from the last 24 h; clear his authority_events.
all_tests : close open queue cards older than 1 h; abandon all open envelopes. Policies kept.
full      : kai + all_tests + seed_all_mongo (canonical seed state).
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timedelta, timezone

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.kernel.trail_mongo import DuplicateIdempotencyKey
from app.kernel.workflow_mongo import resolve_queue_card
from app.seeds import seed_kai_motor_mongo as motor
from app.seeds.seed_users import FICTIONAL_CLIENT_ID

KAI = str(FICTIONAL_CLIENT_ID)
ACTOR = {"kind": "system", "id": "demo_reset", "name": "demo-reset"}
SCOPES = ("kai", "all_tests", "full")


def _aware(v):
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if isinstance(v, datetime) and v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v


async def _emit(db, tid, type_, payload, key):
    async with use_tenant(tid):
        try:
            await append_via_dispatch(db, tid, type_, actor=ACTOR, payload=payload, idempotency_key=key)
        except DuplicateIdempotencyKey:
            pass
        return (await wrap(db).events.find_one({"idempotency_key": key}))["event_id"]


async def _open_envelopes(safe, client_id: str | None, since: datetime | None, before: datetime | None):
    q = {"type": "ClientChangeRequested"}
    if client_id:
        q["payload.client_id"] = client_id
    closed = {e["payload"]["envelope_id"] async for e in safe.events.find(
        {"type": {"$in": ["EnvelopeAbandoned", "EndorsementExecuted"]}}, {"payload.envelope_id": 1})
        if (e.get("payload") or {}).get("envelope_id")}
    out = []
    async for e in safe.events.find(q):
        ts, env = _aware(e.get("ts")), e["payload"]["envelope_id"]
        if env in closed or (since and ts < since) or (before and ts > before):
            continue
        out.append((env, e["payload"].get("policy_id")))
    return out


async def _close_cards(db, tid, cards, reason) -> list[str]:
    done = []
    for c in cards:
        env = c["envelope_id"]
        eid = await _emit(db, tid, "QueueCardClosedStale",
                          {"envelope_id": env, "resolution": "demo_reset", "reason": reason},
                          f"demo_reset:card:{env}:{c.get('hitl_since')}")
        async with use_tenant(tid):
            await wrap(db).proj_queue.update_one(
                {"envelope_id": env, "resolved_at": None},
                {"$set": {"resolution": "demo_reset", "resolution_reason": reason}})
            await resolve_queue_card(db, env, eid)
        done.append(env)
    return done


async def _abandon(db, tid, envs, reason) -> list[str]:
    for env, policy_id in envs:
        await _emit(db, tid, "EnvelopeAbandoned",
                    {"envelope_id": env, "policy_id": policy_id, "reason": reason},
                    f"demo_reset:abandon:{env}")
    return [e for e, _ in envs]


async def _reset_kai(db, tid, now) -> dict:
    safe = wrap(db)
    reason = "Demo reset (Kai) — rehearsal clean-up"
    async with use_tenant(tid):
        res = await safe.policies.update_one(
            {"policy_id": motor.POLICY_ID},
            {"$set": {"schedule": copy.deepcopy(motor.SCHEDULE), "premium_amount": motor.PREMIUM,
                      "premium_status": "current", "status": "managed"}})
        envs = await _open_envelopes(safe, KAI, now - timedelta(hours=24), None)
        cards = [c async for c in safe.proj_queue.find({"client_id": KAI, "resolved_at": None})
                 if _aware(c.get("hitl_since")) and _aware(c["hitl_since"]) >= now - timedelta(hours=24)]
        auth = await safe.authority_events.delete_many({"client_id": KAI})
    return {"policy_104_restored": res.modified_count > 0,
            "envelopes_abandoned": await _abandon(db, tid, envs, reason),
            "cards_closed": await _close_cards(db, tid, cards, reason),
            "authority_events_cleared": auth.deleted_count}


async def _reset_tests(db, tid, now) -> dict:
    safe = wrap(db)
    reason = "Demo reset (all tests) — rehearsal clean-up"
    hour_ago = now - timedelta(hours=1)
    async with use_tenant(tid):
        envs = await _open_envelopes(safe, None, None, hour_ago)
        cards = [c async for c in safe.proj_queue.find({"resolved_at": None})
                 if _aware(c.get("hitl_since")) and _aware(c["hitl_since"]) < hour_ago]
    return {"envelopes_abandoned": await _abandon(db, tid, envs, reason),
            "cards_closed": await _close_cards(db, tid, cards, reason)}


async def run(db, tid: str, scope: str, user_id: str) -> dict:
    now = datetime.now(timezone.utc)
    result: dict = {"scope": scope}
    if scope in ("kai", "full"):
        result["kai"] = await _reset_kai(db, tid, now)
    if scope in ("all_tests", "full"):
        result["all_tests"] = await _reset_tests(db, tid, now)
    if scope == "full":
        from app.seeds.seed_all_mongo import seed_all_mongo
        await seed_all_mongo(db)
        result["seed_all_mongo"] = "applied"
    summary = {k: (len(v) if isinstance(v, list) else v)
               for part in ("kai", "all_tests") for k, v in (result.get(part) or {}).items()}
    result["ledger_event_id"] = await _emit(
        db, tid, "DemoResetExecuted",
        {"scope": scope, "requested_by": user_id, "ts": now.isoformat(), "summary": summary},
        f"demo_reset:{scope}:{uuid.uuid4()}")
    return result
