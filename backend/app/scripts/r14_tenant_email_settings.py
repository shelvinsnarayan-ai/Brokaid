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
    "brand_colour": "#0F1F3D",  # R0.15: button colour; white text must pass 4.5:1
    "allowlist": ["delivered@resend.dev", "luka@underaiter.com"],
}


async def main() -> None:
    load_dotenv()
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    # field-by-field so a logo uploaded in Governance (object storage URL) is kept
    r = await db.tenants.update_one({"slug": "harbourline"},
                                    {"$set": {f"email_settings.{k}": v for k, v in HARBOURLINE.items()}})
    print(f"harbourline matched {r.matched_count}, modified {r.modified_count}")


if __name__ == "__main__":
    asyncio.run(main())
