"""Bounded job: compute the 12 KPIs for every tenant, once per run.

Deterministic. No LLM. Runs under the phase-3 runner:
  - advisory lock 0x6B70696373 ('kpics')
  - max runtime 60s
  - emits KPIDailyComputed under the brokaid-ops tenant per tenant.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import asyncpg

from app.jobs.runner import OPS_TENANT_SLUG, register
from app.kernel.kpis import compute_all, persist_daily
from app.kernel.trail import append

logger = logging.getLogger("brokaid.compute_kpis")


async def compute_for_all_tenants(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT tenant_id::text AS tenant_id, slug FROM tenants "
            "WHERE slug <> $1 ORDER BY slug",
            OPS_TENANT_SLUG,
        )
    ops_row = None
    async with pool.acquire() as c:
        ops_row = await c.fetchrow(
            "SELECT tenant_id::text AS tenant_id FROM tenants WHERE slug = $1",
            OPS_TENANT_SLUG,
        )
    ops_tenant_id = ops_row["tenant_id"] if ops_row else None
    today = datetime.now(timezone.utc).date()

    for r in rows:
        tenant_id = r["tenant_id"]
        values = await compute_all(pool, tenant_id)
        await persist_daily(pool, tenant_id, values, day=today)
        if ops_tenant_id:
            try:
                await append(
                    pool, ops_tenant_id, "KPIDailyComputed",
                    actor={"kind": "system", "id": "kpis", "name": "kpis"},
                    payload={
                        "tenant_id": tenant_id,
                        "day": today.isoformat(),
                        "kpi_ids": [v.kpi_id for v in values],
                    },
                    idempotency_key=f"kpi:daily:{tenant_id}:{today.isoformat()}",
                )
            except asyncpg.UniqueViolationError:
                pass  # already ran for this tenant/day
        logger.info(
            "computed KPIs for tenant %s (%d values)", tenant_id, len(values)
        )


register(
    "compute_kpis",
    compute_for_all_tenants,
    schedule="*/5 * * * *",
    advisory_lock_key=0x6B70696373,   # 'kpics'
    max_runtime_sec=60.0,
    description="Compute and persist the 12 KPI values for every tenant.",
)
