"""Phase R5a-2a — kpis dispatcher (R5c-4b — Mongo-only)."""
from __future__ import annotations

from datetime import date

from app.kernel import kpis_mongo as _mongo_kpis
from app.kernel.kpis import KpiValue


async def compute_all(store, tenant_id: str) -> list[KpiValue]:
    return await _mongo_kpis.compute_all(store, tenant_id)


async def persist_daily(
    store, tenant_id: str, values: list[KpiValue],
    day: date | None = None,
) -> None:
    return await _mongo_kpis.persist_daily(
        store, tenant_id, values, day=day,
    )
