"""Phase R5c-2a — Mongo variant of phase-11 vulnerable-client seed.

Byte-for-byte mirror of `app.seeds.seed_vulnerable` — inserts Kelp
Shipping Co. Pty Ltd (fictional) with `vulnerable_flag=True` and a
Managed Marine Cargo policy, plus the two `ClientEntityCreated` /
`PolicyCreated` ledger events emitted via
`trail_dispatch.append_via_dispatch`.

Client row uses an idempotent `update_one` with `$set` on
`vulnerable_flag` — matches the PG seeder's `ON CONFLICT (client_id)
DO UPDATE SET vulnerable_flag = TRUE` semantics.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError
from app.kernel.trail_mongo import DuplicateIdempotencyKey

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.seeds.seed_vulnerable import (
    VULN_CLIENT_ID, VULN_CLIENT_NAME, VULN_POLICY_ID,
)


async def seed_mongo(
    db: AsyncIOMotorDatabase, tenant_id: str,
) -> dict[str, str]:
    from app.kernel import devmode
    await devmode.require_demo_seed(db, tenant_id)
    now = datetime.now(timezone.utc)
    inception = now
    expiry = now + timedelta(days=365)
    schedule = {
        "cover_types": [
            {"type": "Marine Cargo", "limit_any_one": 5_000_000,
             "aggregate": 15_000_000, "currency": "AUD"},
        ],
        "excess": {"any_one_occurrence": 10_000, "currency": "AUD"},
        "territorial_limits": "Worldwide",
    }
    safe = wrap(db)
    async with use_tenant(tenant_id):
        # Upsert client with vulnerable_flag=True (PG uses ON CONFLICT
        # DO UPDATE — mirror here via update_one with $set + upsert).
        await safe.clients.update_one(
            {"client_id": VULN_CLIENT_ID},
            {"$set": {
                "display_name": VULN_CLIENT_NAME,
                "industry": "shipping",
                "domicile": "Melbourne, AU",
                "jurisdiction_code": "AU",
                "vulnerable_flag": True,
            }},
            upsert=True,
        )
        existing_pol = await safe.policies.find_one({"policy_id": VULN_POLICY_ID})
        if not existing_pol:
            try:
                await safe.policies.insert_one({
                    "policy_id": VULN_POLICY_ID,
                    "client_id": VULN_CLIENT_ID,
                    "status": "managed",
                    "product": "Marine Cargo",
                    "insurer_display": "Fictional Carrier B",
                    "inception": inception,
                    "expiry": expiry,
                    "premium_amount": 4200.00,
                    "premium_currency": "AUD",
                    "schedule": schedule,
                    "imported_from": None,
                })
            except (DuplicateKeyError, DuplicateIdempotencyKey):
                pass
    try:
        await append_via_dispatch(
            db, tenant_id, "ClientEntityCreated",
            actor={"kind": "system", "id": "seed", "name": "phase11-seed"},
            payload={
                "client_id": VULN_CLIENT_ID,
                "display_name": VULN_CLIENT_NAME,
                "industry": "shipping",
                "domicile": "Melbourne, AU",
                "jurisdiction_code": "AU",
                "vulnerable_flag": True,
                "fictional": True,
            },
            idempotency_key=f"seed:client:{VULN_CLIENT_ID}",
        )
    except (DuplicateKeyError, DuplicateIdempotencyKey):
        pass
    try:
        await append_via_dispatch(
            db, tenant_id, "PolicyCreated",
            actor={"kind": "system", "id": "seed", "name": "phase11-seed"},
            payload={
                "policy_id": VULN_POLICY_ID,
                "client_id": VULN_CLIENT_ID,
                "status": "managed",
                "product": "Marine Cargo",
                "insurer_display": "Fictional Carrier B",
                "premium_amount": 4200.0,
                "premium_currency": "AUD",
                "fictional": True,
            },
            idempotency_key=f"seed:policy:{VULN_POLICY_ID}",
        )
    except (DuplicateKeyError, DuplicateIdempotencyKey):
        pass
    return {"client_id": VULN_CLIENT_ID, "policy_id": VULN_POLICY_ID}
