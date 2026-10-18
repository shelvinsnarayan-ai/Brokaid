"""Phase R5c-2a — Mongo variant of phase-5 Tracked policy seed.

Byte-for-byte mirror of `app.seeds.seed_policy_tracked` — inserts
the Cyber Liability tracked policy (manually-entered import) under
`use_tenant(tid)` and emits `PolicyCreated` via
`trail_dispatch.append_via_dispatch`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError
from app.kernel.trail_mongo import DuplicateIdempotencyKey

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.seeds.seed_policy_tracked import INSURER_DISPLAY, POLICY_ID


async def seed_mongo(
    db: AsyncIOMotorDatabase, tenant_id: str, client_id: str,
) -> str:
    from app.kernel import devmode
    await devmode.require_demo_seed(db, tenant_id)
    now = datetime.now(timezone.utc)
    inception = now - timedelta(days=60)
    expiry = now + timedelta(days=305)
    schedule = {
        "cover_types": [
            {"type": "Cyber Liability", "limit_any_one": 2_000_000,
             "aggregate": 2_000_000, "currency": "AUD"},
        ],
        "excess": {"any_one_claim": 10_000, "currency": "AUD"},
        "territorial_limits": "Australia only",
    }
    safe = wrap(db)
    async with use_tenant(tenant_id):
        existing = await safe.policies.find_one({"policy_id": POLICY_ID})
        if not existing:
            try:
                await safe.policies.insert_one({
                    "policy_id": POLICY_ID,
                    "client_id": client_id,
                    "status": "tracked",
                    "product": "Cyber",
                    "insurer_display": INSURER_DISPLAY,
                    "inception": inception,
                    "expiry": expiry,
                    "premium_amount": 2_200.00,
                    "premium_currency": "AUD",
                    "schedule": schedule,
                    "imported_from": "manual_entry_burmac",
                })
            except (DuplicateKeyError, DuplicateIdempotencyKey):
                pass
    try:
        await append_via_dispatch(
            db, tenant_id, "PolicyCreated",
            actor={"kind": "system", "id": "seed", "name": "phase5-seed"},
            payload={
                "policy_id": POLICY_ID,
                "client_id": client_id,
                "status": "tracked",
                "product": "Cyber",
                "insurer_display": INSURER_DISPLAY,
                "premium_amount": 2200.0,
                "premium_currency": "AUD",
                "imported_from": "manual_entry_burmac",
                "schedule": schedule,
                "mocked": True,
                "fictional": True,
            },
            idempotency_key=f"seed:policy_created:{POLICY_ID}",
        )
    except (DuplicateKeyError, DuplicateIdempotencyKey):
        pass
    return POLICY_ID
