"""R3.1 — realistic, varied cover for the Harbourline demo client (demo tenant only; re-runnable; ledgered).

- Motor (…0104): two vehicles with values — Toyota Hilux SR5 (agreed A$48,000) and Isuzu D-Max (market A$39,500).
- Marine liability (…0101): public liability limit A$20M.
- Tools and equipment (…0150): sum insured A$45,000, excess A$250.
- Business pack — contents (…0151, new): contents A$120,000, business interruption A$250,000.
- Look-alike duplicates (two current policies with the same product) are archived, keeping the latest expiry.
Usage: python -m app.scripts.r31_demo_cover
"""
# tenant-safe: system-only (Harbourline resolved by slug; every write is tenant-scoped)
import asyncio
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

CID = "00000000-0000-4000-8000-000000000001"
P = "00000000-0000-4000-8000-000000000"


async def main() -> None:
    from app.kernel import devmode
    from app.kernel.renewals import _event
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    t = await db.tenants.find_one({"slug": "harbourline"})
    if not t or not devmode.is_demo_row(t):
        raise SystemExit("Harbourline demo tenant not found")
    tid, now = t["tenant_id"], datetime.now(timezone.utc)

    async def log(kind, payload):
        await _event(db, tid, "DemoCoverSeeded", {"change": kind, **payload}, f"r31:{kind}:{payload.get('policy_id')}:{now.date()}", "r31", "operator")

    motor = await db.policies.find_one({"tenant_id": tid, "policy_id": P + "104"})
    if motor:
        vs = (motor.get("schedule") or {}).get("vehicles") or []
        for v in vs:
            if v.get("rego") in ("ABC123", "ABC-123"):
                v.update({"make": "Toyota", "model": "Hilux SR5", "year": 2021, "agreed_value": 48000})
            elif v.get("rego") in ("DEF456", "DEF-456"):
                v.update({"market_value": 39500})
        await db.policies.update_one({"_id": motor["_id"]}, {"$set": {"schedule.vehicles": vs}})
        await log("motor_values", {"policy_id": motor["policy_id"], "vehicles": [v.get("rego") for v in vs]})
    market = {"XYZ-789": 52000, "XYZ789": 52000, "DEF-456": 39500, "DEF456": 39500, "ABC-123": 48000, "ABC123": 48000}
    async for mp in db.policies.find({"tenant_id": tid, "client_id": CID, "status": "managed", "schedule.vehicles": {"$exists": True}}):
        vs = mp["schedule"]["vehicles"]
        changed = False
        for v in vs:
            if not (v.get("agreed_value") or v.get("market_value")) and v.get("rego") in market:
                v["market_value"] = market[v["rego"]]
                changed = True
        if changed:
            await db.policies.update_one({"_id": mp["_id"]}, {"$set": {"schedule.vehicles": vs}})
            await log("vehicle_values", {"policy_id": mp["policy_id"]})
    ml = await db.policies.find_one({"tenant_id": tid, "policy_id": P + "101"})
    if ml and not any(isinstance(c.get("limit_any_one"), (int, float)) for c in (ml.get("schedule") or {}).get("cover_types") or []):
        await db.policies.update_one({"_id": ml["_id"]}, {"$set": {"schedule.cover_types": [
            {"type": "Public liability", "limit_any_one": 20000000}, {"type": "Products liability", "limit_any_one": 20000000}]}})
        await log("liability_limit", {"policy_id": ml["policy_id"]})
    tools = await db.policies.find_one({"tenant_id": tid, "policy_id": P + "150"})
    if tools:
        await db.policies.update_one({"_id": tools["_id"]}, {"$set": {
            "schedule.sum_insured": 45000, "schedule.excess": {"any_one_occurrence": 250, "currency": "AUD"},
            "schedule.cover_types": [{"type": "Tools and equipment — all risks", "sum_insured": 45000}],
            "schedule.territorial_limits": "Anywhere in Australia"}})
        await log("tools_sum_insured", {"policy_id": tools["policy_id"]})
    if not await db.policies.find_one({"tenant_id": tid, "policy_id": P + "151"}):
        await db.policies.insert_one({
            "tenant_id": tid, "policy_id": P + "151", "client_id": CID, "status": "managed", "product": "Business pack — contents",
            "insurer_display": "Fictional Carrier A", "policy_number": "BP-0151-FICT", "inception": now - timedelta(days=120),
            "expiry": now + timedelta(days=245), "premium_amount": 1860,
            "schedule": {"sum_insured": 120000, "excess": {"any_one_occurrence": 500, "currency": "AUD"},
                         "cover_types": [{"type": "Contents", "sum_insured": 120000}, {"type": "Business interruption", "sum_insured": 250000},
                                         {"type": "Theft (forcible entry)", "sum_insured": 20000}],
                         "territorial_limits": "Workshop, 12 Harris St, Pyrmont NSW"},
            "created_at": now})
        await log("business_pack_created", {"policy_id": P + "151"})
    seen = {}
    async for p in db.policies.find({"tenant_id": tid, "client_id": CID, "status": {"$in": ["managed", "in_force", "tracked"]}}).sort("expiry", -1):
        k = (p.get("product") or "").strip().lower()
        if k in seen:
            await db.policies.update_one({"_id": p["_id"]}, {"$set": {"status": "archived", "archived_reason": "demo_lookalike_duplicate",
                                                                      "archived_at": now}})
            await log("duplicate_archived", {"policy_id": p["policy_id"], "kept": seen[k]})
            print("archived duplicate", p["policy_id"], p.get("product"))
        else:
            seen[k] = p["policy_id"]
    print("current:", sorted(seen))


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
