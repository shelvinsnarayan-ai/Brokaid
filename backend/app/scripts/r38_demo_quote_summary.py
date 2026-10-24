"""R3.8 — repair-quote summary on the existing CLM-2026-000013 Northside quote (Harbourline demo only; idempotent; ledgered).

Values are the ones printed on the sample PDF. The PDF has no quote date, so the card shows the date received.
Usage: python -m app.scripts.r38_demo_quote_summary
"""
# tenant-safe: system-only (Harbourline resolved by slug; every write is tenant-scoped)
import asyncio
import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

REF = "CLM-2026-000013"
FILENAME = "Northside-Smash-Repairs-Quote-Q-1042.pdf"
VALUES = {"repairer": "Northside Smash Repairs", "total": "2340.00", "quote_no": "Q-1042", "quote_date": None}


async def seed(db) -> dict:
    from app.kernel import devmode, repair_quote
    from app.kernel.renewals import _event
    t = await db.tenants.find_one({"slug": "harbourline"})
    if not t or not devmode.is_demo_row(t):
        raise SystemExit("Harbourline demo tenant not found")
    tid = t["tenant_id"]
    claim = await db.claims.find_one({"tenant_id": tid, "claim_reference": REF}, {"_id": 0})
    doc = next(d for d in claim.get("docs") or [] if d.get("filename") == FILENAME and d.get("label") == "repair_quote" and not d.get("test"))
    new = repair_quote.clean(VALUES)
    new["source"] = {k: "typed" for k in new["source"]} | {"corrected_by": "r38_demo"}
    if doc.get("quote") == new:
        return {"claim_reference": REF, "file_id": doc["file_id"], "replayed": True}
    await db.claims.update_one({"tenant_id": tid, "claim_id": claim["claim_id"], "docs.file_id": doc["file_id"]}, {"$set": {"docs.$.quote": new}})
    await _event(db, tid, "RepairQuoteSummaryUpdated", {"claim_id": claim["claim_id"], "file_id": doc["file_id"], "old": doc.get("quote"), "new": new,
                 "by": "r38_demo", "role": "operator", "demo": True}, f"r38:quote_summary:{doc['file_id']}", "r38", "operator")
    from app.scripts.demo_refresh_unread import refresh
    return {"claim_reference": REF, "file_id": doc["file_id"], "quote": new, "unread": await refresh(db)}


async def main() -> None:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    print(await seed(db))


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
