"""Bounded chain-verify job.

Walks each tenant's chain, records a ChainVerified event, exits 0
on success. Advisory lock keyed per-tenant so it can't overlap with
appends. Max runtime 60s per tenant.

Run standalone:
    python -m app.jobs.chain_verify

No always-on scheduling. The phase-3 scheduler will invoke this.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

from app.kernel.trail import append, read_events
from app.kernel.verifier import verify_events

logger = logging.getLogger("brokaid.chain_verify")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MAX_RUNTIME_SECONDS_PER_TENANT = 60

VERIFIER_PY = Path(__file__).resolve()


def _verifier_version() -> str:
    """Short signature of this verifier binary."""
    return hashlib.sha256(VERIFIER_PY.read_bytes()).hexdigest()[:16]


async def verify_tenant(pool: asyncpg.Pool, slug: str) -> bool:
    """Verify a tenant's full chain and emit ChainVerified. Returns ok."""
    start = datetime.now(timezone.utc)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT tenant_id FROM tenants WHERE slug = $1", slug
        )
        if row is None:
            logger.error("unknown tenant: %s", slug)
            return False
        tenant_id = str(row["tenant_id"])

        # advisory lock to serialise with appends
        await conn.execute(
            "SELECT pg_advisory_lock(hashtextextended($1, 0))", tenant_id
        )
        try:
            _, events = await read_events(pool, slug)
            # serialise events to plain dicts for verify_events
            payload = [
                {
                    "event_id": e.event_id, "tenant_id": e.tenant_id, "seq": e.seq,
                    "ts": e.ts, "actor": e.actor, "type": e.type, "payload": e.payload,
                    "payload_jcs": e.payload_jcs, "prev_hash": e.prev_hash,
                    "event_hash": e.event_hash, "idempotency_key": e.idempotency_key,
                    "mocked": e.mocked,
                }
                for e in events
            ]
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            if elapsed > MAX_RUNTIME_SECONDS_PER_TENANT:
                logger.warning("tenant %s: exceeded max runtime (%.1fs)", slug, elapsed)
                return False

            result = verify_events(payload)
            if not result.ok:
                logger.error(
                    "tenant %s FAILED at seq %s (%s): %s",
                    slug, result.failure_seq, result.failure_event_id, result.reason,
                )

            # append the ChainVerified event regardless (ok=False is recorded too)
            await append(
                pool, tenant_id, "ChainVerified",
                actor={"kind": "system", "id": "chain_verify", "name": "trAIl-verifier"},
                payload={
                    "from_seq": events[0].seq if events else 0,
                    "to_seq": events[-1].seq if events else 0,
                    "ok": result.ok,
                    "root_hash": result.root_hash,
                    "verifier_version": _verifier_version(),
                },
                idempotency_key=f"chain-verify:{slug}:{start.isoformat()}",
            )
            return result.ok
        finally:
            await conn.execute(
                "SELECT pg_advisory_unlock(hashtextextended($1, 0))", tenant_id
            )


async def _main() -> int:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    dsn = os.environ["DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT slug FROM tenants ORDER BY slug")
        slugs = [r["slug"] for r in rows]
        overall_ok = True
        for s in slugs:
            ok = await verify_tenant(pool, s)
            overall_ok = overall_ok and ok
            logger.info("tenant %s: %s", s, "OK" if ok else "FAIL")
        return 0 if overall_ok else 1
    finally:
        await pool.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
