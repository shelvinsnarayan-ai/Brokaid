"""Phase 16 — CLI entrypoint for the deterministic CI seed pair.

Usage:
    python -m app.seeds.ci_deterministic

Runs idempotently against the DSN in MONGO_URL (falls back to the
`settings.database_url`). Never invoked on default server boot — this
is the explicit trigger the orchestrator uses to land ci-alpha and
ci-beta into the running preview DB.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys


from app.seeds.ci_deterministic import seed_ci_deterministic
from app.settings import get_settings


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("brokaid.ci_seed")


async def _main() -> int:
    dsn = os.environ.get("MONGO_URL") or get_settings().database_url
    if not dsn:
        logger.error("no DSN — set MONGO_URL")
        return 2
    pool = await None  # PG deleted R5c-4a(dsn=dsn, min_size=1, max_size=4)
    try:
        result = await seed_ci_deterministic(pool)
    finally:
        await pool.close()
    for t in result.get("tenants", []):
        logger.info(
            "tenant %s (%s) genesis=%s",
            t["slug"], t["tenant_id"], t["genesis_hash"],
        )
    logger.info("done — %d tenant(s)", len(result.get("tenants", [])))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
