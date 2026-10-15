"""R0.7 — rename Harbourline-tenant demo logins @burmac.example → @harbourline.example.
# tenant-safe: system-only  (one-off migration; legacy BurMac tenant untouched)

Idempotent; user_id preserved. Usage: python -m app.scripts.r07_harbourline_emails
"""
import asyncio
import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient


async def main() -> None:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    h = await db.tenants.find_one({"slug": "harbourline"})
    n = 0
    async for u in db.users.find({"tenant_id": h["tenant_id"], "email": {"$regex": r"@burmac\.example$"}}):
        new = u["email"].replace("@burmac.example", "@harbourline.example")
        await db.users.update_one({"_id": u["_id"]}, {"$set": {"email": new, "email_renamed_from": u["email"]}})
        n += 1
        print(f"{u['email']} -> {new} (user_id {u['user_id']})")
    left = await db.users.count_documents({"tenant_id": h["tenant_id"], "email": {"$regex": "@burmac.example$"}})
    print(f"renamed {n}; @burmac.example left in Harbourline users: {left}")


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
