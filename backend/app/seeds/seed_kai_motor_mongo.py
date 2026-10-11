"""R0.2 — Kai's Motor Fleet policy (synthetic) for the vehicle-swap spine.

Idempotent: `find_one` guard on insert + idempotency key on PolicyCreated.
Kai's Marine Liability (…101) and Cyber (…102) are untouched.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.kernel.trail_mongo import DuplicateIdempotencyKey

POLICY_ID = "00000000-0000-4000-8000-000000000104"
INSURER_DISPLAY = "Fictional Carrier A"
PRODUCT = "Motor — Commercial Vehicle"
PREMIUM = 3_800.00

GARAGE_A = "12 Wharf Road, Fictional Bay NSW 2000"
GARAGE_B = "4 Slipway Lane, Fictional Bay NSW 2000"

SCHEDULE = {
    "limits": {"comprehensive_motor": 1_000_000},
    "cover_types": [
        {"type": "Comprehensive Motor", "limit_any_one": 1_000_000,
         "currency": "AUD"},
    ],
    "excess": {"any_one_occurrence": 1_000, "currency": "AUD"},
    "vehicles": [
        {"rego": "ABC123", "vin": "MOCKVIN0000000ABC1", "make": "Nissan",
         "model": "Navara", "year": 2020, "body_type": "Dual cab ute",
         "use": "business", "garaged_address": GARAGE_A, "driver_assigned": "D1"},
        {"rego": "DEF456", "vin": "MOCKVIN0000000DEF4", "make": "Isuzu",
         "model": "D-Max", "year": 2019, "body_type": "Single cab tray",
         "use": "business", "garaged_address": GARAGE_B, "driver_assigned": "D2"},
    ],
    "drivers": [
        {"driver_id": "D1", "name": "Alex Fictional", "dob": "1984-03-14",
         "licence_number": "MOCK10001", "licence_class": "C"},
        {"driver_id": "D2", "name": "Sam Fictional", "dob": "1991-11-02",
         "licence_number": "MOCK10002", "licence_class": "LR"},
    ],
    "addresses": [GARAGE_A, GARAGE_B],
}


async def seed_mongo(db: AsyncIOMotorDatabase, tenant_id: str, client_id: str) -> str:
    now = datetime.now(timezone.utc)
    safe = wrap(db)
    async with use_tenant(tenant_id):
        if not await safe.policies.find_one({"policy_id": POLICY_ID}):
            try:
                await safe.policies.insert_one({
                    "policy_id": POLICY_ID,
                    "client_id": client_id,
                    "status": "managed",
                    "product": PRODUCT,
                    "insurer_display": INSURER_DISPLAY,
                    "inception": now - timedelta(days=30),
                    "expiry": now + timedelta(days=335),
                    "premium_amount": PREMIUM,
                    "premium_currency": "AUD",
                    "premium_status": "current",
                    "schedule": SCHEDULE,
                    "imported_from": None,
                    "fictional": True,
                })
            except (DuplicateKeyError, DuplicateIdempotencyKey):
                pass
    try:
        await append_via_dispatch(
            db, tenant_id, "PolicyCreated",
            actor={"kind": "system", "id": "seed", "name": "r02-seed"},
            payload={
                "policy_id": POLICY_ID, "client_id": client_id,
                "status": "managed", "product": PRODUCT,
                "insurer_display": INSURER_DISPLAY,
                "premium_amount": PREMIUM, "premium_currency": "AUD",
                "schedule": SCHEDULE, "mocked": True, "fictional": True,
            },
            idempotency_key=f"seed:policy_created:{POLICY_ID}",
        )
    except (DuplicateKeyError, DuplicateIdempotencyKey):
        pass
    return POLICY_ID
