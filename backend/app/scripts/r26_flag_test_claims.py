"""R2.6 — flag the testers' claims CLM-2026-000011/12 (lodged with an unknown "vehicle_rego" field, so no vehicle) as test data.
# tenant-safe: system-only  (one-off, re-runnable; sets the test flag and records one ledger event per claim)

Usage: python -m app.scripts.r26_flag_test_claims [CLM-...]
"""
import asyncio
import os
import sys

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

REFS = ("CLM-2026-000011", "CLM-2026-000012")


async def main(refs) -> None:
    from app.kernel.trail_dispatch import append_via_dispatch
    from app.kernel.tenant_ctx import use_tenant
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    async for c in db.claims.find({"claim_reference": {"$in": list(refs)}}, {"_id": 0, "claim_id": 1, "claim_reference": 1, "tenant_id": 1, "test": 1}):
        if not c.get("test"):
            await db.claims.update_one({"claim_id": c["claim_id"], "tenant_id": c["tenant_id"]}, {"$set": {"test": True}})
            async with use_tenant(c["tenant_id"]):
                await append_via_dispatch(db, c["tenant_id"], "ClaimFlaggedAsTest", actor={"kind": "system", "id": "r26", "name": "build"},
                                          payload={"claim_id": c["claim_id"], "claim_reference": c["claim_reference"],
                                                   "reason": "tester claim lodged with an unknown field (no vehicle)"},
                                          idempotency_key=f"claim_flag_test:{c['claim_id']}")
        print(f"{c['claim_reference']}  {c['claim_id']}  test=True  (was {bool(c.get('test'))})")


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main(sys.argv[1:] or REFS))
