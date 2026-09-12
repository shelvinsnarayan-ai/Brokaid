"""Bounded timer sweep job.

Fires TimerFired for every proj_workflow_waits row whose fire_at is
past now() and which isn't yet resolved. Advisory-locked so
overlapping invocations are no-ops.

Run standalone:
    python -m app.jobs.timer_sweep
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

from app.kernel.trail import append

logger = logging.getLogger("brokaid.timer_sweep")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MAX_RUNTIME_SEC = 25   # 30s schedule → 5s guard band
ADVISORY_KEY = 0x74696D65727377  # ascii 'timersw'


async def sweep_once(pool: asyncpg.Pool) -> int:
    """Fire any ready timers. Returns count fired."""
    async with pool.acquire() as conn:
        got = await conn.fetchval(
            "SELECT pg_try_advisory_lock($1)", ADVISORY_KEY
        )
        if not got:
            logger.info("another sweep is in flight — exiting quietly")
            return 0
        try:
            fired = 0
            start = datetime.now(timezone.utc)

            # Iterate all tenants (RLS bypass via tenants table which has no RLS).
            slugs = await conn.fetch(
                "SELECT tenant_id, slug FROM tenants ORDER BY slug"
            )
            for t in slugs:
                if (datetime.now(timezone.utc) - start).total_seconds() > MAX_RUNTIME_SEC:
                    logger.warning("max runtime exceeded — stopping mid-sweep")
                    break
                tenant_id = str(t["tenant_id"])
                async with conn.transaction():
                    await conn.execute(
                        "SELECT set_config('app.tenant_id', $1, true)", tenant_id
                    )
                    waits = await conn.fetch(
                        """
                        SELECT workflow_id, step
                          FROM proj_workflow_waits
                         WHERE tenant_id = $1::uuid
                           AND fire_at IS NOT NULL
                           AND fire_at <= NOW()
                           AND NOT resolved
                        """,
                        tenant_id,
                    )
                for w in waits:
                    try:
                        await append(
                            pool, tenant_id, "TimerFired",
                            actor={"kind": "system", "id": "timer_sweep", "name": "sweep"},
                            payload={
                                "workflow_id": w["workflow_id"],
                                "step": w["step"],
                            },
                            idempotency_key=f"timer:{w['workflow_id']}:{w['step']}",
                        )
                        fired += 1
                    except asyncpg.UniqueViolationError:
                        # Already fired — benign.
                        pass
            return fired
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", ADVISORY_KEY)


async def _main() -> int:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    dsn = os.environ["DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    try:
        fired = await sweep_once(pool)
        logger.info("timer_sweep: %d timers fired", fired)
        return 0
    except Exception as e:
        logger.error("timer_sweep failed: %r", e)
        return 1
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
