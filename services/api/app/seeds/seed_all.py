"""Phase-5 seed loader — users + org + client + both policies. Idempotent."""
from __future__ import annotations

import asyncpg

from app.seeds import (
    seed_client,
    seed_org,
    seed_policy_managed,
    seed_policy_tracked,
    seed_users,
)


async def seed_all(pool: asyncpg.Pool) -> dict:
    users_result = await seed_users.seed(pool)
    tenant_id = users_result["tenant_id"]
    brokerage_id = users_result["brokerage_id"]

    await seed_org.seed(pool, tenant_id, brokerage_id)
    client_id = await seed_client.seed(pool, tenant_id)
    managed_id = await seed_policy_managed.seed(pool, tenant_id, client_id)
    tracked_id = await seed_policy_tracked.seed(pool, tenant_id, client_id)

    return {
        **users_result,
        "client_id": client_id,
        "policy_ids": {"managed": managed_id, "tracked": tracked_id},
    }
