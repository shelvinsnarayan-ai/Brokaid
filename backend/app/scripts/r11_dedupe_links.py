"""R1.1 — remove duplicate user_client_links / memberships rows (keep the newest per key), then add unique indexes.
Re-runnable. Ledger untouched. Usage: python -m app.scripts.r11_dedupe_links [--dry-run]
"""
# tenant-safe: system-only — one-off maintenance script; scopes every query by tenant explicitly.
import asyncio
import os
import sys

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

KEYS = {"user_client_links": ("user_id", "tenant_id", "client_id"),
        "memberships": ("tenant_id", "user_id", "client_id", "role")}


async def dedupe(db, dry: bool = False) -> dict:
    out = {}
    for coll, key in KEYS.items():
        removed = 0
        groups = db[coll].aggregate([
            {"$sort": {"_id": -1}},  # newest ObjectId first
            {"$group": {"_id": {k: f"${k}" for k in key}, "ids": {"$push": "$_id"},
                        "live": {"$push": {"$ifNull": ["$revoked_at", None]}}, "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 1}}}])
        async for g in groups:
            # keep the newest valid (not revoked) row, else the newest
            keep = next((i for i, r in zip(g["ids"], g["live"]) if r is None), g["ids"][0])
            drop = [i for i in g["ids"] if i != keep]
            if not dry:
                removed += (await db[coll].delete_many({"_id": {"$in": drop}})).deleted_count
            else:
                removed += len(drop)
        out[coll] = removed
    if not dry:
        await db.user_client_links.create_index([("user_id", 1), ("tenant_id", 1), ("client_id", 1)],
                                                unique=True, name="ucl_user_tenant_client_uniq")
        await db.memberships.create_index([("tenant_id", 1), ("user_id", 1), ("client_id", 1), ("role", 1)],
                                          unique=True, name="mem_tenant_user_client_role_uniq")
    return out


async def main() -> None:
    load_dotenv()
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    before = await db.user_client_links.count_documents({})
    res = await dedupe(db, "--dry-run" in sys.argv)
    print(f"links before {before}, removed {res}, links after {await db.user_client_links.count_documents({})}")


if __name__ == "__main__":
    asyncio.run(main())
