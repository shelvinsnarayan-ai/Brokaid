"""Before a demo: leave exactly one unread broker reply on the demo client's CLM-2026-000013 claim thread (Harbourline only).

Moves the main demo owner's seen marker to just before the latest broker reply. Idempotent; writes a ledger row only when it
changes something. Usage: python -m app.scripts.demo_refresh_unread
"""
# tenant-safe: system-only (Harbourline resolved by slug; every write is tenant-scoped)
import asyncio
import os
from datetime import timedelta

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

REF = "CLM-2026-000013"
OWNER = "owner@fictionalclient.example"


async def refresh(db) -> dict:
    from app.kernel import claim_threads as ct, devmode, inbox
    from app.kernel.renewals import _event
    t = await db.tenants.find_one({"slug": "harbourline"})
    if not t or not devmode.is_demo_row(t):
        raise SystemExit("Harbourline demo tenant not found")
    tid = t["tenant_id"]
    claim = await db.claims.find_one({"tenant_id": tid, "claim_reference": REF}, {"_id": 0})
    owner = await db.users.find_one({"email": OWNER})
    thread = await ct.thread_for(db, tid, claim["client_id"], claim["claim_id"])
    last = [m for m in thread["messages"] if m.get("reply_id")][-1]
    at = last["at"]
    since = at.replace(microsecond=at.microsecond // 1000 * 1000) - timedelta(milliseconds=1)  # Mongo keeps milliseconds
    key = {"tenant_id": tid, "user_id": owner["user_id"], "thread_id": thread["thread_id"]}
    cur = await db.ask_seen.find_one(key)
    if cur and inbox._utc(cur["last_seen_at"]) == since:
        return {"claim_reference": REF, "unread_reply_id": last["reply_id"], "changed": False}
    await db.ask_seen.update_one(key, {"$set": {"last_seen_at": since}}, upsert=True)
    await _event(db, tid, "DemoUnreadRefreshed", {"thread_id": thread["thread_id"], "reply_id": last["reply_id"],
                                                  "was": inbox._utc(cur["last_seen_at"]).isoformat() if cur else None,
                                                  "now": since.isoformat()},
                 f"demo_refresh_unread:{thread['thread_id']}:{since.isoformat()}:{cur['last_seen_at'] if cur else 'none'}", "demo", "operator")
    return {"claim_reference": REF, "unread_reply_id": last["reply_id"], "changed": True}


async def main() -> None:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    print(await refresh(db))


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
