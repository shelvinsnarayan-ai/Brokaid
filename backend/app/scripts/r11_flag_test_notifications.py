"""R0.11A — flag test-generated notifications so demo bells stay clean (re-runnable; flags only).
# tenant-safe: system-only  (one-off maintenance)

Flags (test=True) every Harbourline notification that is tied to a flagged/test thread or reply,
that was created at/after --since (ISO time of a test run start), or that belongs to an --envelope.
Usage: python -m app.scripts.r11_flag_test_notifications [--since ISO] [--envelope ID ...]
"""
import argparse
import asyncio
import os
from datetime import datetime

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient


async def main(since: str | None, envelopes: list[str]) -> None:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    tid = (await db.tenants.find_one({"slug": "harbourline"}))["tenant_id"]
    flags = [f async for f in db.test_flags.find({"tenant_id": tid})]
    test_threads = [e["payload"]["envelope_id"] async for e in db.events.find(
        {"tenant_id": tid, "type": "ClientQuestionAsked", "payload.test": True}, {"payload.envelope_id": 1})]
    ors = [{"thread_id": {"$in": [f["ref"] for f in flags if f["kind"] == "thread"] + test_threads}},
           {"reply_id": {"$in": [f["ref"] for f in flags if f["kind"] == "reply"]}}]
    for env in envelopes:
        ors.append({"dedupe_key": {"$regex": f":{env}$"}})
    if since:
        ors.append({"created_at": {"$gte": datetime.fromisoformat(since)}})
    r = await db.notifications.update_many({"tenant_id": tid, "test": {"$ne": True}, "$or": ors},
                                           {"$set": {"test": True, "test_flagged_at": datetime.now().astimezone()}})
    left = await db.notifications.count_documents({"tenant_id": tid, "test": {"$ne": True}})
    print(f"newly flagged {r.modified_count}; visible demo notifications left {left}")


if __name__ == "__main__":
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--since")
    ap.add_argument("--envelope", action="append", default=[], help="flag alerts for this envelope/thread id")
    a = ap.parse_args()
    asyncio.run(main(a.since, a.envelope))
