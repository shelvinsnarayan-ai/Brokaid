"""Phase R5c-2a — Mongo variant of phase-5 Managed policy seed.

Byte-for-byte mirror of `app.seeds.seed_policy_managed` — inserts
the Marine Liability policy under `use_tenant(tid)` and emits
`PolicyCreated` via `trail_dispatch.append_via_dispatch`.
Idempotent via `find_one` guard + DuplicateKeyError catch.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError
from app.kernel.trail_mongo import DuplicateIdempotencyKey

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.seeds.seed_policy_managed import INSURER_DISPLAY, POLICY_ID


async def seed_mongo(
    db: AsyncIOMotorDatabase, tenant_id: str, client_id: str,
) -> str:
    now = datetime.now(timezone.utc)
    inception = now
    expiry = now + timedelta(days=365)
    schedule = {
        # `limits` mirrors `cover_types[*].limit_any_one` so the
        # endorsement flow (which writes `schedule.limits.<slug>`)
        # and the certificate/verifier (which read cover_types)
        # cannot drift. Both representations seed at 10,000,000.
        "limits": {
            "public_liability": 10_000_000,
            "products_liability": 10_000_000,
        },
        "cover_types": [
            {"type": "Public Liability", "limit_any_one": 10_000_000,
             "aggregate": 20_000_000, "currency": "AUD"},
            {"type": "Products Liability", "limit_any_one": 10_000_000,
             "aggregate": 20_000_000, "currency": "AUD"},
        ],
        "excess": {"any_one_occurrence": 25_000, "currency": "AUD"},
        "territorial_limits": "Worldwide excl. USA/Canada",
    }
    safe = wrap(db)
    async with use_tenant(tenant_id):
        existing = await safe.policies.find_one({"policy_id": POLICY_ID})
        if not existing:
            try:
                await safe.policies.insert_one({
                    "policy_id": POLICY_ID,
                    "client_id": client_id,
                    "status": "managed",
                    "product": "Marine Liability",
                    "insurer_display": INSURER_DISPLAY,
                    "inception": inception,
                    "expiry": expiry,
                    "premium_amount": 8_500.00,
                    "premium_currency": "AUD",
                    "schedule": schedule,
                    "imported_from": None,
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
                "status": "managed",
                "product": "Marine Liability",
                "insurer_display": INSURER_DISPLAY,
                "premium_amount": 8500.0,
                "premium_currency": "AUD",
                "schedule": schedule,
                "mocked": True,
                "fictional": True,
            },
            idempotency_key=f"seed:policy_created:{POLICY_ID}",
        )
    except (DuplicateKeyError, DuplicateIdempotencyKey):
        pass
    return POLICY_ID
