"""R0.10a — flag leftover testing-agent Ask threads/replies on the Harbourline tenant as test data.
# tenant-safe: system-only  (one-off, re-runnable; flags only — the ledger is never touched)

Usage: python -m app.scripts.r10_flag_test_asks
"""
import asyncio
import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

THREAD_RX = re.compile(r"BROWSERTEST|^JT test|^\s*$")
REPLY_RX = re.compile(r"BROWSERREPLY|JTREPLY|^R09 |\(\d{6}\)$|\(QA\)$")


async def main() -> None:
    from app.kernel import inbox
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    tid = (await db.tenants.find_one({"slug": "harbourline"}))["tenant_id"]
    flags = []
    for t in await inbox.load_threads(db, tid, include_tests=True):
        if any(m["who"] == "client" and THREAD_RX.search(m["text"] or "") for m in t["messages"]):
            flags.append(("thread", t["thread_id"], (t["subject"] or "(empty)")[:60]))
        flags += [("reply", m["reply_id"], m["text"][:60]) for m in t["messages"]
                  if m.get("reply_id") and REPLY_RX.search(m["text"] or "")]
    new = 0
    for kind, ref, why in flags:
        r = await db.test_flags.update_one(
            {"tenant_id": tid, "kind": kind, "ref": ref},
            {"$setOnInsert": {"reason": why, "flagged_at": datetime.now(timezone.utc)}}, upsert=True)
        new += bool(r.upserted_id)
        print(f"{kind:6} {ref[:18]:18} {why}")
    print(f"flags matched {len(flags)}, newly flagged {new}")


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
