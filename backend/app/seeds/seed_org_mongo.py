"""Phase R5c-2a — Mongo variant of phase-5 org seed.

Byte-for-byte mirror of `app.seeds.seed_org` — emits two ledger
events (LicenseeCreated + BrokerageCreated) via
`trail_dispatch.append_via_dispatch`. Idempotency guaranteed by the
events collection unique index on `(tenant_id, idempotency_key)`;
duplicate-append is a no-op.
"""
from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.kernel.trail_dispatch import append_via_dispatch
from app.kernel.trail_mongo import DuplicateIdempotencyKey
from app.seeds.seed_org import LICENSEE_ABN, LICENSEE_NAME


async def seed_mongo(
    db: AsyncIOMotorDatabase, tenant_id: str, brokerage_id: str,
) -> None:
    try:
        await append_via_dispatch(
            db, tenant_id, "LicenseeCreated",
            actor={"kind": "system", "id": "seed", "name": "phase5-seed"},
            payload={
                "licensee_name": LICENSEE_NAME,
                "licensee_abn": LICENSEE_ABN,
                "fictional": True,
            },
            idempotency_key=f"seed:licensee:{LICENSEE_ABN}",
        )
    except (DuplicateKeyError, DuplicateIdempotencyKey):
        pass
    try:
        await append_via_dispatch(
            db, tenant_id, "BrokerageCreated",
            actor={"kind": "system", "id": "seed", "name": "phase5-seed"},
            payload={
                "brokerage_id": brokerage_id,
                "licensee_abn": LICENSEE_ABN,
                "fictional": True,
            },
            idempotency_key=f"seed:brokerage:{brokerage_id}",
        )
    except (DuplicateKeyError, DuplicateIdempotencyKey):
        pass
