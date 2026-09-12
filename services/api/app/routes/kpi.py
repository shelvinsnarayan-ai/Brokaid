"""Operator KPI routes — /api/o/kpi/summary and /kpi/:id/events."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth.sessions import Session
from app.kernel import kpis
from app.middleware.session import require_operator_session


router = APIRouter(prefix="/api/o/kpi")


def _parse(raw) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


@router.get("/summary")
async def summary(
    request: Request,
    session: Session = Depends(require_operator_session),
) -> dict:
    pool = request.app.state.pool
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", session.tenant_id
            )
            today = datetime.now(timezone.utc).date()
            rows = await c.fetch(
                """
                SELECT kpi_id, value_scalar, value_meta, delta_prev,
                       spark_series, computed_at
                  FROM proj_kpi_daily
                 WHERE tenant_id = $1::uuid AND day = $2
                 ORDER BY kpi_id ASC
                """,
                session.tenant_id, today,
            )
    if not rows or len(rows) < 12:
        # Compute on-demand — the /5-min job may not have fired yet.
        values = await kpis.compute_all(pool, session.tenant_id)
        await kpis.persist_daily(pool, session.tenant_id, values)
        return {
            "day": datetime.now(timezone.utc).date().isoformat(),
            "kpis": [v.to_dict() for v in values],
        }

    def _row_to_dict(r) -> dict:
        return {
            "kpi_id": int(r["kpi_id"]),
            "scalar": float(r["value_scalar"]),
            "meta": _parse(r["value_meta"]),
            "delta_prev": None if r["delta_prev"] is None else float(r["delta_prev"]),
            "spark_series": [float(x) for x in (r["spark_series"] or [])],
        }
    return {
        "day": rows[0]["computed_at"].date().isoformat(),
        "kpis": [_row_to_dict(r) for r in rows],
    }


@router.get("/{kpi_id}/events")
async def drill(
    kpi_id: int, request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(require_operator_session),
) -> dict:
    if kpi_id not in kpis.DRILLABLE:
        raise HTTPException(status_code=400, detail="not_drillable_in_this_build")
    fn = kpis.COMPUTATIONS[kpi_id]
    pool = request.app.state.pool
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", session.tenant_id
            )
            v = await fn(c, session.tenant_id)
    events = list(v.meta.get("events") or [])
    total = len(events)
    page = events[offset: offset + limit]
    return {
        "kpi_id": kpi_id,
        "label": v.meta.get("label"),
        "scalar": v.scalar,
        "count": total,
        "events": page,
    }
