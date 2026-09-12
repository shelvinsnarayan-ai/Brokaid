"""Phase-5 policy seed — the Managed policy (Marine Liability)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

from app.kernel.trail import append


POLICY_ID = "00000000-0000-4000-8000-000000000101"
INSURER_DISPLAY = "Fictional Carrier A"


async def seed(pool: asyncpg.Pool, tenant_id: str, client_id: str) -> str:
    now = datetime.now(timezone.utc)
    inception = now
    expiry = now + timedelta(days=365)
    schedule = {
        "cover_types": [
            {"type": "Public Liability", "limit_any_one": 10_000_000,
             "aggregate": 20_000_000, "currency": "AUD"},
            {"type": "Products Liability", "limit_any_one": 10_000_000,
             "aggregate": 20_000_000, "currency": "AUD"},
        ],
        "excess": {"any_one_occurrence": 25_000, "currency": "AUD"},
        "territorial_limits": "Worldwide excl. USA/Canada",
    }
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            await c.execute(
                """
                INSERT INTO policies (policy_id, tenant_id, client_id, status,
                                      product, insurer_display, inception, expiry,
                                      premium_amount, premium_currency, schedule,
                                      imported_from)
                VALUES ($1::uuid, $2::uuid, $3::uuid, 'managed',
                        'Marine Liability', $4, $5, $6, $7, 'AUD', $8::jsonb, NULL)
                ON CONFLICT (policy_id) DO NOTHING
                """,
                POLICY_ID, tenant_id, client_id, INSURER_DISPLAY,
                inception, expiry, 8_500.00, json.dumps(schedule),
            )
    try:
        await append(
            pool, tenant_id, "PolicyCreated",
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
    except asyncpg.UniqueViolationError:
        pass
    return POLICY_ID
