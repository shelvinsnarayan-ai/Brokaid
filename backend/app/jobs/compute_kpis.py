"""Bounded job: compute the 12 KPIs for every tenant (dispatch-aware).

Deterministic. No LLM. Runs under the phase-3 runner:
  - advisory lock 0x6B70696373 ('kpics')
  - max runtime 60s
  - emits KPIDailyComputed under the brokaid-ops tenant per tenant.

R5c-3: `compute_for_all_tenants(store)` accepts either an (mongo)
Pool or a Motor DB. Tenant enumeration goes through
`jobs._tenants.list_tenants`; KPI compute + persist go through
`kpis_dispatch`; ledger emission goes through
`trail_dispatch.append_via_dispatch`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

from app.jobs._tenants import find_by_slug, list_tenants
from app.jobs.runner import OPS_TENANT_SLUG, register
from app.kernel.kpis_dispatch import compute_all, persist_daily
from app.kernel.trail_dispatch import append_via_dispatch

logger = logging.getLogger("brokaid.compute_kpis")


async def compute_for_all_tenants(store) -> None:
    rows = await list_tenants(store, exclude_slug=OPS_TENANT_SLUG)
    ops_row = await find_by_slug(store, OPS_TENANT_SLUG)
    ops_tenant_id = ops_row["tenant_id"] if ops_row else None
    today = datetime.now(timezone.utc).date()

    for r in rows:
        tenant_id = r["tenant_id"]
        values = await compute_all(store, tenant_id)
        await persist_daily(store, tenant_id, values, day=today)
        if ops_tenant_id:
            try:
                await append_via_dispatch(
                    store, ops_tenant_id, "KPIDailyComputed",
                    actor={"kind": "system", "id": "kpis", "name": "kpis"},
                    payload={
                        "tenant_id": tenant_id,
                        "day": today.isoformat(),
                        "kpi_ids": [v.kpi_id for v in values],
                    },
                    idempotency_key=(
                        f"kpi:daily:{tenant_id}:{today.isoformat()}"
                    ),
                )
            except DuplicateKeyError:
                # Idempotent on the (tenant, day) key — already ran.
                pass
        logger.info(
            "computed KPIs for tenant %s (%d values)",
            tenant_id, len(values),
        )


register(
    "compute_kpis",
    compute_for_all_tenants,
    schedule="*/5 * * * *",
    advisory_lock_key=0x6B70696373,   # 'kpics'
    max_runtime_sec=60.0,
    description="Compute and persist the 12 KPI values for every tenant.",
)
