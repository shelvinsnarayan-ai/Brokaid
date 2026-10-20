"""R3.2 — one payment due for the Harbourline demo client (demo tenant only; re-runnable; ledgered).

A broker invoice payable (Owed, own CRN) on the business pack, due 7 days from the run. Re-running refreshes the due date
while it is still Owed and never creates a second payable.
Usage: python -m app.scripts.r32_demo_payment_due
"""
# tenant-safe: system-only (Harbourline resolved by slug; every write is tenant-scoped)
import asyncio
import os
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

CID = "00000000-0000-4000-8000-000000000001"
PREFERRED = "00000000-0000-4000-8000-000000000151"
ENVELOPE = "r32-demo-invoice"


async def seed(db) -> dict:
    from app.kernel import devmode, payments
    from app.kernel.renewals import _event
    t = await db.tenants.find_one({"slug": "harbourline"})
    if not t or not devmode.is_demo_row(t):
        raise SystemExit("Harbourline demo tenant not found")
    tid, now = t["tenant_id"], datetime.now(timezone.utc)
    pol = await db.policies.find_one({"tenant_id": tid, "policy_id": PREFERRED, "status": "managed"}) or \
        await db.policies.find_one({"tenant_id": tid, "client_id": CID, "status": "managed", "test": {"$ne": True}})
    if not pol:
        raise SystemExit("No current demo policy to invoice")
    p = await payments.ensure_payable(db, tid, envelope_id=ENVELOPE, policy_id=pol["policy_id"], amount="465.00", kind="invoice")
    if payments.state_of(p) == "owed":
        due = (date.today() + timedelta(days=7)).isoformat()
        await db.payables.update_one({"tenant_id": tid, "payable_id": p["payable_id"]}, {"$set": {"due_date": due}})
        p["due_date"] = due
    await _event(db, tid, "DemoPaymentDueSeeded", {"payable_id": p["payable_id"], "policy_id": pol["policy_id"], "amount": "465.00"},
                 f"r32:payment_due:{p['payable_id']}:{now.date()}", "r32", "operator")
    return p


async def main() -> None:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    p = await seed(db)
    print("payable", p["payable_id"], "crn", p["crn"], "state", p["state"], "due", p["due_date"])


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
