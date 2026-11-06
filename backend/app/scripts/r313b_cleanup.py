"""R3.13b: flag the T48 tester artifacts as test (Harbourline only; idempotent; ledgered).

Invite token, membership, bookkeeper account (login disabled), uploaded test schedule, and the timeline events they produced.
Usage: python -m app.scripts.r313b_cleanup
"""
# tenant-safe: system-only (Harbourline resolved by slug; every read/write is tenant-scoped)
import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

INVITE = "6bcf7ac6-9487-4caa-9ae6-1f804b165dd7"
MEMBERSHIP = "1a66f91d-64e3-4413-9157-621ec9d4f711"
EMAIL = "t48.bookkeeper@example.com"
DOC = "d292be87-18fd-4320-b5b7-d98ee7eb6dbb"
WHY = "T48 tester artifact (R3.13 review)"


async def run(db) -> dict:
    from app.kernel import renewals as rn
    tid = (await db.tenants.find_one({"slug": "harbourline"}, {"tenant_id": 1}))["tenant_id"]
    now, out = datetime.now(timezone.utc), {}
    user = await db.users.find_one({"tenant_id": tid, "email": EMAIL}, {"user_id": 1})
    uid = user["user_id"] if user else None
    out["invite"] = (await db.auth_tokens.update_one({"tenant_id": tid, "token_id": INVITE, "test": {"$ne": True}},
                                                     {"$set": {"test": True, "revoked_at": now}})).modified_count
    out["membership"] = (await db.memberships.update_one({"tenant_id": tid, "membership_id": MEMBERSHIP, "test": {"$ne": True}},
                                                         {"$set": {"test": True, "status": "removed"}})).modified_count
    out["user"] = (await db.users.update_one({"tenant_id": tid, "email": EMAIL, "test": {"$ne": True}},
                                             {"$set": {"test": True, "disabled": True}})).modified_count
    out["doc"] = (await db.policy_documents.update_one({"tenant_id": tid, "doc_id": DOC, "test": {"$ne": True}},
                                                       {"$set": {"test": True, "staff_only": True}})).modified_count
    refs = [r for r in (INVITE, MEMBERSHIP, DOC, uid, EMAIL) if r]
    q = {"tenant_id": tid, "$or": [{f"payload.{k}": {"$in": refs}} for k in
                                   ("token_id", "invite_id", "membership_id", "user_id", "doc_id", "email", "invitee_email")]}
    events = [e["event_id"] async for e in db.events.find(q, {"event_id": 1})]
    flagged = 0
    for ref in events:
        r = await db.test_flags.update_one({"tenant_id": tid, "kind": "event", "ref": ref},
                                           {"$setOnInsert": {"reason": WHY, "flagged_at": now}}, upsert=True)
        flagged += bool(r.upserted_id)
    out["events_found"], out["events_flagged"] = len(events), flagged
    if any(out[k] for k in ("invite", "membership", "user", "doc", "events_flagged")):
        await rn._event(db, tid, "DemoTestDataFlagged", {"kind": "t48", "refs": refs, "events": events, "reason": WHY},
                        f"r313b:t48:{flagged}:{out['user']}", "r313b", "operator")
    return out


async def main():
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    print(await run(db))


if __name__ == "__main__":
    asyncio.run(main())
