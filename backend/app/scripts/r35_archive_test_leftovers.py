"""R3.4b — flag leftover test-shaped data on the Harbourline demo client as test (demo tenant only; idempotent; ledgered).

Nothing is deleted. Claims get test=true; Ask/claim threads and broker replies get a test_flags row (the existing
mechanism that hides them from clients). Each new flag writes a DemoTestDataFlagged ledger row.
Matches: ZZZ- regos, "T34"-style tags, QA / BROWSERTEST / "JT test" / "please ignore" / the word "test".
Usage: python -m app.scripts.r35_archive_test_leftovers
"""
# tenant-safe: system-only (Harbourline resolved by slug; every read/write is tenant-scoped)
import asyncio
import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

CID = "00000000-0000-4000-8000-000000000001"
RX = re.compile(r"ZZZ-?\d|\bT\d{2}\b|\bQA\b|BROWSERTEST|^JT test|please ignore|\btest\b", re.I)


async def _flag(db, tid, _event, kind, ref, why) -> bool:
    r = await db.test_flags.update_one({"tenant_id": tid, "kind": kind, "ref": ref},
                                       {"$setOnInsert": {"reason": f"r35 demo: {why}", "flagged_at": datetime.now(timezone.utc)}}, upsert=True)
    if r.upserted_id:
        await _event(db, tid, "DemoTestDataFlagged", {"kind": kind, "ref": ref, "reason": why}, f"r35:flag:{kind}:{ref}", "r35", "operator")
    return bool(r.upserted_id)


async def run(db) -> dict:
    from app.kernel import devmode
    from app.kernel.renewals import _event
    t = await db.tenants.find_one({"slug": "harbourline"})
    if not t or not devmode.is_demo_row(t):
        raise SystemExit("Harbourline demo tenant not found")
    tid, out = t["tenant_id"], {"claims": [], "threads": 0, "replies": 0}
    async for c in db.claims.find({"tenant_id": tid, "client_id": CID, "test": {"$ne": True}}):
        inc = c.get("incident") or {}
        text = " ".join(str(x) for x in (c.get("claim_reference"), c.get("description"), inc.get("rego"), inc.get("what")) if x)
        if RX.search(text):
            await db.claims.update_one({"tenant_id": tid, "claim_id": c["claim_id"]},
                                       {"$set": {"test": True, "test_reason": "r35 demo leftover test data"}})
            await _event(db, tid, "DemoTestDataFlagged", {"kind": "claim", "ref": c["claim_id"], "claim_reference": c.get("claim_reference"),
                                                          "reason": "test-shaped claim"}, f"r35:flag:claim:{c['claim_id']}", "r35", "operator")
            out["claims"].append(c.get("claim_reference"))
    async for e in db.events.find({"tenant_id": tid, "type": "ClientQuestionAsked", "payload.client_id": CID}).sort("seq", 1):
        p = e["payload"]
        if RX.search(p.get("message") or ""):
            out["threads"] += await _flag(db, tid, _event, "thread", p.get("thread_id") or p.get("envelope_id"), "test-shaped message")
    async for m in db.thread_messages.find({"tenant_id": tid, "client_id": CID}):
        if RX.search(m.get("body") or ""):
            out["replies"] += await _flag(db, tid, _event, "reply", m["reply_id"], "test-shaped broker reply")
    return out


async def main() -> None:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    print(await run(db))


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
