"""R2.7 — put at least one Harbourline demo row in every Renewal review bucket (demo tenant only; re-runnable; ledgered).

Usage: python -m app.scripts.r27_seed_renewal_review
  - Marine Cargo (…0103): expiry today+3 (Sydney), no renewal → 7 days or less, "Not started"
  - Home (…0105): expiry today+10 → 14 days or less
  - Kai's Tools and equipment (…0150) stays where it is (≈16 days → 30 days or less)
  - Motor private (…0106): expiry today−2 with a renewal held after a suspension (lapse_hold) → Expired, Confirm lapse
"""
# tenant-safe: system-only (demo tenant resolved by slug; every write is tenant-scoped)
import asyncio
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

PLAN = (("00000000-0000-4000-8000-000000000103", 3, False), ("00000000-0000-4000-8000-000000000105", 10, False),
        ("00000000-0000-4000-8000-000000000106", -2, True))


async def main() -> None:
    from app.db.tenant_safe import wrap
    from app.kernel import holidays
    from app.kernel import renewals as rn
    from app.kernel.tenant_ctx import use_tenant
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    t = await db.tenants.find_one({"slug": "harbourline"})
    if not t or not t.get("is_demo", True) or t.get("kind") == "platform":
        raise SystemExit("Harbourline demo tenant not found")
    tid, safe = t["tenant_id"], wrap(db)
    today = datetime.now(timezone.utc).astimezone(holidays.SYD).replace(hour=17, minute=0, second=0, microsecond=0)
    for pid, days, held in PLAN:
        exp = (today + timedelta(days=days)).astimezone(timezone.utc)
        async with use_tenant(tid):
            p = await safe.policies.find_one({"policy_id": pid})
            if not p:
                print("missing", pid)
                continue
            await safe.policies.update_one({"policy_id": pid}, {"$set": {"expiry": exp, "inception": exp - timedelta(days=365), "status": "managed"}})
            r = await safe.renewals.find_one({"policy_id": pid}, {"_id": 0})
        if held and not r:
            r = await rn.start(db, tid, await rn._policy(db, tid, pid), now=exp - timedelta(days=60), by="r27-seed", role="operator")
        async with use_tenant(tid):
            if r:
                await safe.renewals.update_one({"renewal_id": r["renewal_id"]}, {"$set": {"expiry": exp, "lapse_hold": held, "test": False}})
        await rn._event(db, tid, "DemoRenewalReviewSeeded", {"policy_id": pid, "expiry": exp.isoformat(), "days": days, "lapse_hold": held,
                        "renewal_id": (r or {}).get("renewal_id")}, f"r27_seed:{pid}:{exp.date().isoformat()}", "r27-seed", "operator")
        print(f"{p.get('product'):32s} expiry {exp.astimezone(holidays.SYD):%d/%m/%Y}  days {days:+d}  renewal {(r or {}).get('renewal_id') or '— (not started)'}"
              f"{'  lapse_hold' if held else ''}")


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
