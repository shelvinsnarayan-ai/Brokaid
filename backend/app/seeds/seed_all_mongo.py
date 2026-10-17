"""Phase R5c-2a — Mongo composer for the full phase-5/7/11 seed.

Mirror of `app.seeds.seed_all.seed_all` — same composition order,
same fixture data (Harbour Marine + Kelp Shipping, Marine Liability
+ Cyber + Marine Cargo policies, 30 historical Approvals + 5 queue
cards), all under `use_tenant(tid)` on the Mongo store.

Idempotent: sub-seeders all guard on either a `find_one` check or
a `SeedMarker` idempotency-key, so a second invocation is a no-op.
"""
from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.seeds import (
    seed_burmac_extras_mongo,
    seed_client_mongo,
    seed_historical_mongo,
    seed_org_mongo,
    seed_policy_managed_mongo,
    seed_kai_motor_mongo,
    seed_memberships_mongo,
    seed_policy_tracked_mongo,
    seed_things_mongo,
    seed_inbox_mongo,
    seed_users_mongo,
    seed_vulnerable_mongo,
)


async def seed_all_mongo(db: AsyncIOMotorDatabase) -> dict:
    users_result = await seed_users_mongo.seed_mongo(db)
    tenant_id = users_result["tenant_id"]
    brokerage_id = users_result["brokerage_id"]
    from app.kernel import devmode
    if not await devmode.is_demo_tenant(db, tenant_id):  # SEC-001b: dev seeds only ever touch a demo tenant
        raise RuntimeError("dev seed target is not a demo/test tenant; refusing to seed")

    await seed_org_mongo.seed_mongo(db, tenant_id, brokerage_id)
    client_id = await seed_client_mongo.seed_mongo(db, tenant_id)
    await seed_memberships_mongo.seed_mongo(db, tenant_id)
    managed_id = await seed_policy_managed_mongo.seed_mongo(
        db, tenant_id, client_id,
    )
    tracked_id = await seed_policy_tracked_mongo.seed_mongo(
        db, tenant_id, client_id,
    )
    await seed_kai_motor_mongo.seed_mongo(db, tenant_id, client_id)
    things = await seed_things_mongo.seed_mongo(db, tenant_id, client_id)
    await seed_inbox_mongo.seed_mongo(db, tenant_id, client_id)

    hist = await seed_historical_mongo.seed_historical_decisions_mongo(
        db, tenant_id,
    )
    cards = await seed_historical_mongo.seed_queue_cards_mongo(
        db, tenant_id, client_id,
    )

    vuln = await seed_vulnerable_mongo.seed_mongo(db, tenant_id)

    # R7-Demo-Seed — burmac-only extras (extra clients, evidence-rich
    # queue cards, verifier token, KPI-history backfill, Kai's pending
    # endorsement). Runs LAST so it never re-orders any hash-pinned
    # events under burmac, and it doesn't touch ci-alpha / ci-beta.
    burmac_extras = await seed_burmac_extras_mongo.seed_burmac_extras(
        db, tenant_id,
    )

    return {
        **users_result,
        "client_id": client_id,
        "policy_ids": {
            "managed": managed_id, "tracked": tracked_id,
            "vulnerable": vuln["policy_id"],
        },
        "vulnerable_client_id": vuln["client_id"],
        "historical": hist,
        "queue_cards": cards,
        "burmac_extras": burmac_extras,
        "things": things,
    }
