"""R3.2 — one payment due for the Harbourline demo client (demo tenant only; re-runnable; ledgered).

A broker invoice payable (Owed, own CRN) on the business pack, due 7 days from the run. Re-running refreshes the due date
while it is still Owed and never creates a second payable.
R3.3: `--due-in N` moves that same invoice's due date to N days from today (never creates a payable) so the Home reminder shows.
Usage: python -m app.scripts.r32_demo_payment_due [--due-in 2]
"""
# tenant-safe: system-only (Harbourline resolved by slug; every write is tenant-scoped)
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

CID = "00000000-0000-4000-8000-000000000001"
PREFERRED = "00000000-0000-4000-8000-000000000151"
ENVELOPE = "r32-demo-invoice"


async def seed(db, due_in: int | None = None) -> dict:
    from app.kernel import devmode, holidays, payments
    from app.kernel.renewals import _event
    t = await db.tenants.find_one({"slug": "harbourline"})
    if not t or not devmode.is_demo_row(t):
        raise SystemExit("Harbourline demo tenant not found")
    tid, now = t["tenant_id"], datetime.now(timezone.utc)
    pol = await db.policies.find_one({"tenant_id": tid, "policy_id": PREFERRED, "status": "managed"}) or \
        await db.policies.find_one({"tenant_id": tid, "client_id": CID, "status": "managed", "test": {"$ne": True}})
    if not pol:
        raise SystemExit("No current demo policy to invoice")
    if due_in is None:
        p = await payments.ensure_payable(db, tid, envelope_id=ENVELOPE, policy_id=pol["policy_id"], amount="465.00", kind="invoice")
    else:
        p = await db.payables.find_one({"tenant_id": tid, "envelope_id": ENVELOPE}, {"_id": 0})
        if not p:
            raise SystemExit("Run without --due-in first: the demo invoice does not exist")
    if payments.state_of(p) == "owed":
        due = (datetime.now(holidays.SYD).date() + timedelta(days=7 if due_in is None else due_in)).isoformat()
        await db.payables.update_one({"tenant_id": tid, "payable_id": p["payable_id"]}, {"$set": {"due_date": due}})
        p["due_date"] = due
    await _event(db, tid, "DemoPaymentDueSeeded", {"payable_id": p["payable_id"], "policy_id": p["policy_id"], "amount": "465.00",
                                                   "due_date": p["due_date"]},
                 f"r32:payment_due:{p['payable_id']}:{now.date()}:{due_in or 7}", "r32", "operator")
    return p


async def main() -> None:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    args = sys.argv[1:]
    p = await seed(db, int(args[args.index("--due-in") + 1]) if "--due-in" in args else None)
    print("payable", p["payable_id"], "crn", p["crn"], "state", p["state"], "due", p["due_date"])


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
