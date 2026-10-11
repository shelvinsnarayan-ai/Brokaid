"""R0.2 one-time cleanup (idempotent). Run: python -m app.scripts.r02_cleanup

1. Close pre-R0.2-format open queue cards (hitl_since < cutoff, proposal not
   an AgentOutputProduced carrying confidence) as resolution=stale, with a
   reason event. Nothing is deleted.
2. Mark Round 0 orphan envelope 9b7186e2 as abandoned with a reason event.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.kernel.trail_mongo import DuplicateIdempotencyKey
from app.kernel.workflow_mongo import resolve_queue_card

CUTOFF = datetime(2026, 10, 11, 6, 0, tzinfo=timezone.utc)
FLAG = "pre_r02_proposal_format"
ORPHAN = "9b7186e2-c71c-4d68-a8f3-5f3045f8baa0"
ORPHAN_REASON = ("Round 0 inspection artefact — made-up vehicle on marine policy; "
                 "cleared under R0.2 safeguards")
ACTOR = {"kind": "system", "id": "r02_cleanup", "name": "admin-cleanup"}


def _aware(v) -> datetime | None:
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if isinstance(v, datetime) and v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v


async def _emit(db, tid: str, type_: str, payload: dict, key: str) -> str:
    safe = wrap(db)
    async with use_tenant(tid):
        try:
            await append_via_dispatch(db, tid, type_, actor=ACTOR, payload=payload,
                                      idempotency_key=key)
        except DuplicateIdempotencyKey:
            pass
        ev = await safe.events.find_one({"idempotency_key": key})
    return ev["event_id"]


async def _close_card(db, tid: str, envelope_id: str, event_id: str, reason: str) -> bool:
    safe = wrap(db)
    async with use_tenant(tid):
        res = await safe.proj_queue.update_one(
            {"envelope_id": envelope_id, "resolved_at": None},
            {"$set": {"resolution": "stale", "resolution_reason": reason, "flag": FLAG}},
        )
        await resolve_queue_card(db, envelope_id, event_id)
    return res.modified_count > 0


async def _is_pre_r02(safe, card: dict) -> bool:
    ev = await safe.events.find_one({"event_id": card.get("proposal_ref")})
    return not ev or ev["type"] != "AgentOutputProduced" or not (ev.get("payload") or {}).get("confidence")


async def run(db, tid: str) -> dict:
    safe = wrap(db)
    stale: list[str] = []
    async with use_tenant(tid):
        cards = [c async for c in safe.proj_queue.find({"resolved_at": None})]
        for c in cards:
            since = _aware(c.get("hitl_since"))
            if since and since < CUTOFF and await _is_pre_r02(safe, c):
                stale.append(c["envelope_id"])
    reason = "Pre-R0.2 proposal format (no evidence/confidence components); closed as stale"
    for env in stale:
        eid = await _emit(db, tid, "QueueCardClosedStale",
                          {"envelope_id": env, "resolution": "stale", "flag": FLAG,
                           "reason": reason}, f"r02:stale:{env}")
        await _close_card(db, tid, env, eid, reason)
    async with use_tenant(tid):
        orphan_src = await safe.events.find_one({"payload.envelope_id": ORPHAN})
    orphan_closed = False
    if orphan_src:
        eid = await _emit(db, tid, "EnvelopeAbandoned",
                          {"envelope_id": ORPHAN,
                           "policy_id": (orphan_src.get("payload") or {}).get("policy_id"),
                           "reason": ORPHAN_REASON}, f"r02:abandon:{ORPHAN}")
        await _close_card(db, tid, ORPHAN, eid, ORPHAN_REASON)
        orphan_closed = True
    return {"stale_cards_closed": stale, "orphan_abandoned": orphan_closed}


async def main() -> None:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    tenant = await db.tenants.find_one({"slug": "burmac"})
    print(await run(db, tenant["tenant_id"]))


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(main())
