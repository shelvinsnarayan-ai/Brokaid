"""R3.13c: flag QA/TEST_/cleanup ledger events as test so AE/Principal timelines skip them (Harbourline only; idempotent; ledgered).

An event is a test artifact when a human-written field (message, note, reason, term label, title, draft) contains "TEST_",
the word "QA" (upper case), or "cleanup"; or it is itself demo/test bookkeeping (Demo*Flagged*).
Usage: python -m app.scripts.r313c_flag_qa_events [--dry-run]
"""
# tenant-safe: system-only (Harbourline resolved by slug; every read/write is tenant-scoped)
import asyncio
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

async def run(db, dry: bool = False) -> dict:
    from app.kernel import renewals as rn
    from app.kernel.event_text import is_test_note as is_test
    tid = (await db.tenants.find_one({"slug": "harbourline"}, {"tenant_id": 1}))["tenant_id"]
    flagged = {f["ref"] async for f in db.test_flags.find({"tenant_id": tid, "kind": "event"}, {"ref": 1})}
    hits = [e async for e in db.events.find({"tenant_id": tid}, {"_id": 0, "event_id": 1, "type": 1, "payload": 1}) if is_test(e)]
    new = [e for e in hits if e["event_id"] not in flagged]
    if dry:
        return {"matched": len(hits), "new": [(e["type"], e["event_id"][:8]) for e in new]}
    now = datetime.now(timezone.utc)
    for e in new:
        await db.test_flags.update_one({"tenant_id": tid, "kind": "event", "ref": e["event_id"]},
                                       {"$setOnInsert": {"reason": "QA/TEST_/cleanup note (R3.13c)", "flagged_at": now}}, upsert=True)
    if any(not e["type"].startswith("Demo") for e in new):  # bookkeeping events are flagged silently, so reruns settle
        await rn._event(db, tid, "DemoTestDataFlagged", {"kind": "qa_events", "events": [e["event_id"] for e in new],
                        "reason": "QA/TEST_/cleanup note (R3.13c)"}, f"r313c:qa:{len(flagged)}:{len(new)}", "r313c", "operator")
    return {"matched": len(hits), "flagged_now": len(new)}


async def main():
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    print(await run(db, "--dry-run" in sys.argv))


if __name__ == "__main__":
    asyncio.run(main())
