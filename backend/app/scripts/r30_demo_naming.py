"""R3.0 — demo naming + Cover duplicates (Harbourline + burmac-sandbox only; re-runnable; ledgered).

1. Seeded demo personas get role names: Client, Client bookkeeper, Account executive, Principal, Operator, Sandbox principal.
   Emails and ids are unchanged.
2. Demo-renewal terms that the demo reset cancelled (cancelled_reason=demo_reset) are archived so the client Cover never
   shows duplicates.

Usage: python -m app.scripts.r30_demo_naming
"""
# tenant-safe: system-only (demo tenants resolved by slug; every write is tenant-scoped)
import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

NAMES = {"harbourline": {"owner@fictionalclient.example": "Client", "bookkeeper@fictionalclient.example": "Client bookkeeper",
                         "ae@harbourline.example": "Account executive", "principal@harbourline.example": "Principal",
                         "operator@harbourline.example": "Operator"},
         "burmac-sandbox": {"principal@burmac-sandbox.example": "Sandbox principal"}}


async def main() -> None:
    from app.kernel import devmode
    from app.kernel.renewals import _event
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    for slug, names in NAMES.items():
        t = await db.tenants.find_one({"slug": slug})
        if not t or not devmode.is_demo_row(t):
            print(f"{slug}: not a demo tenant here — skipped")
            continue
        tid = t["tenant_id"]
        for email, name in names.items():
            u = await db.users.find_one({"tenant_id": tid, "email": email}, {"user_id": 1, "display_name": 1})
            if u and u.get("display_name") != name:
                await db.users.update_one({"_id": u["_id"]}, {"$set": {"display_name": name}})
                await _event(db, tid, "DemoPersonaRenamed", {"user_id": u["user_id"], "email": email, "from": u.get("display_name"),
                             "to": name}, f"r30_rename:{u['user_id']}:{name}", "r30", "operator")
                print(f"{slug}: {email}  {u.get('display_name')!r} → {name!r}")
        if slug != "harbourline":
            continue
        async for p in db.policies.find({"tenant_id": tid, "status": "cancelled", "cancelled_reason": "demo_reset"}, {"policy_id": 1}):
            await db.policies.update_one({"_id": p["_id"]}, {"$set": {"status": "archived", "archived_at": datetime.now(timezone.utc),
                                                                      "archived_reason": "demo_duplicate_term"}})
            await _event(db, tid, "DemoPolicyTermArchived", {"policy_id": p["policy_id"], "previous_status": "cancelled",
                         "reason": "demo_duplicate_term"}, f"r30_archive:{p['policy_id']}", "r30", "operator")
            print(f"{slug}: archived demo term {p['policy_id']}")


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
