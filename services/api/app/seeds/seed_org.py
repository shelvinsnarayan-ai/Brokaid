"""Phase-5 org seed — licensee + brokerage confirmations.

Idempotent. Brokerage already seeded in phase 4 (`seed_users.py`);
this file records the licensee metadata and emits ledger events
under the BurMac tenant.
"""
from __future__ import annotations

import asyncpg

from app.kernel.trail import append


LICENSEE_NAME = "Fictional Licensee Pty Ltd"
LICENSEE_ABN = "FICTIONAL_LICENSEE_ABN_0000"


async def seed(pool: asyncpg.Pool, tenant_id: str, brokerage_id: str) -> None:
    try:
        await append(
            pool, tenant_id, "LicenseeCreated",
            actor={"kind": "system", "id": "seed", "name": "phase5-seed"},
            payload={
                "licensee_name": LICENSEE_NAME,
                "licensee_abn": LICENSEE_ABN,
                "fictional": True,
            },
            idempotency_key=f"seed:licensee:{LICENSEE_ABN}",
        )
    except asyncpg.UniqueViolationError:
        pass
    try:
        await append(
            pool, tenant_id, "BrokerageCreated",
            actor={"kind": "system", "id": "seed", "name": "phase5-seed"},
            payload={
                "brokerage_id": brokerage_id,
                "licensee_abn": LICENSEE_ABN,
                "fictional": True,
            },
            idempotency_key=f"seed:brokerage:{brokerage_id}",
        )
    except asyncpg.UniqueViolationError:
        pass
