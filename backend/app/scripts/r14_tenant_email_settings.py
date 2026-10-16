"""R0.14 — per-brokerage email settings on the Harbourline tenant record. Re-runnable; touches no other tenant.
Usage: python -m app.scripts.r14_tenant_email_settings
"""
import asyncio
import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

HARBOURLINE = {
    "sender_name": "Harbourline Insurance Brokers",
    "reply_to": None,
    "brand_colour": "#F2B01E",
    "logo_url": "/brand/wordmark-harbour-bold.svg",
    "allowlist": ["delivered@resend.dev", "luka@underaiter.com"],
}


async def main() -> None:
    load_dotenv()
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    r = await db.tenants.update_one({"slug": "harbourline"}, {"$set": {"email_settings": HARBOURLINE}})
    print(f"harbourline matched {r.matched_count}, modified {r.modified_count}")


if __name__ == "__main__":
    asyncio.run(main())
