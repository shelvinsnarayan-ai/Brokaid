"""R0.8 — re-runnable backfill: reply-by promises for existing Harbourline Ask threads.
# tenant-safe: system-only  (one-off migration)

Usage: python -m app.scripts.r08_inbox_promises
"""
import asyncio
import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient


async def main() -> None:
    from app.kernel import inbox
    from app.seeds import seed_inbox_mongo
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    h = await db.tenants.find_one({"slug": "harbourline"})
    kai = await db.clients.find_one({"tenant_id": h["tenant_id"], "display_name": {"$regex": "^Harbour Marine"}})
    await seed_inbox_mongo.seed_mongo(db, h["tenant_id"], kai["client_id"])
    print(await inbox.backfill(db, h["tenant_id"]))


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
