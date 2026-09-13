"""Phase-5/7 seed loader — users + org + client + policies + historical."""
from __future__ import annotations

import asyncpg

from app.seeds import (
    seed_client,
    seed_historical,
    seed_org,
    seed_policy_managed,
    seed_policy_tracked,
    seed_users,
    seed_vulnerable,
)


async def seed_all(pool: asyncpg.Pool) -> dict:
    users_result = await seed_users.seed(pool)
    tenant_id = users_result["tenant_id"]
    brokerage_id = users_result["brokerage_id"]

    await seed_org.seed(pool, tenant_id, brokerage_id)
    client_id = await seed_client.seed(pool, tenant_id)
    managed_id = await seed_policy_managed.seed(pool, tenant_id, client_id)
    tracked_id = await seed_policy_tracked.seed(pool, tenant_id, client_id)

    # Phase 7 additions — historical decisions + pending queue cards.
    hist = await seed_historical.seed_historical_decisions(pool, tenant_id)
    cards = await seed_historical.seed_queue_cards(pool, tenant_id, client_id)

    # Phase 11 — vulnerable client + Managed policy for the vulnerable-flag beat.
    vuln = await seed_vulnerable.seed(pool, tenant_id)

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
    }
