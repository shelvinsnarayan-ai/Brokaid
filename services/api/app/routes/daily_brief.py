"""Operator Daily Brief — projection-only. No narrative sentences."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.auth.sessions import Session
from app.kernel import kpis
from app.middleware.session import require_operator_session


router = APIRouter(prefix="/api/o")


@router.get("/daily_brief")
async def daily_brief(
    request: Request,
    session: Session = Depends(require_operator_session),
) -> dict[str, Any]:
    pool = request.app.state.pool
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", session.tenant_id
            )
            t = await c.fetchrow(
                "SELECT display_name FROM tenants WHERE tenant_id = $1::uuid",
                session.tenant_id,
            )
            new_matters_rows = await c.fetch(
                "SELECT event_id::text AS event_id FROM events "
                " WHERE tenant_id = $1::uuid AND type = 'MatterCreated' "
                "   AND ts >= NOW() - INTERVAL '24 hours'",
                session.tenant_id,
            )
            endorsements_rows = await c.fetch(
                "SELECT event_id::text AS event_id FROM events "
                " WHERE tenant_id = $1::uuid AND type = ANY($2::text[]) "
                "   AND ts >= NOW() - INTERVAL '24 hours'",
                session.tenant_id,
                ["EndorsementInstructed", "EndorsementQuoted", "QuotePresented"],
            )
            claims_rows = await c.fetch(
                "SELECT event_id::text AS event_id FROM events "
                " WHERE tenant_id = $1::uuid AND type = ANY($2::text[]) "
                "   AND ts >= NOW() - INTERVAL '24 hours'",
                session.tenant_id,
                ["ClaimOpened", "ClaimUpdated"],
            )
            hitl_size_row = await c.fetchrow(
                "SELECT COUNT(*) AS n FROM proj_queue "
                " WHERE tenant_id = $1::uuid AND resolved_at IS NULL",
                session.tenant_id,
            )
            overdue_row = await c.fetchrow(
                "SELECT COUNT(*) AS n FROM proj_queue "
                " WHERE tenant_id = $1::uuid AND resolved_at IS NULL "
                "   AND sla_deadline < NOW()",
                session.tenant_id,
            )
            # KPI deltas — pull today's snapshot.
            today = datetime.now(timezone.utc).date()
            kpi_rows = await c.fetch(
                "SELECT kpi_id, value_scalar, delta_prev FROM proj_kpi_daily "
                " WHERE tenant_id = $1::uuid AND day = $2 ORDER BY kpi_id ASC",
                session.tenant_id, today,
            )
    if not kpi_rows:
        values = await kpis.compute_all(pool, session.tenant_id)
        await kpis.persist_daily(pool, session.tenant_id, values)
        kpi_rows = [
            {"kpi_id": v.kpi_id, "value_scalar": v.scalar, "delta_prev": v.delta_prev}
            for v in values
        ]

    kpis_of_note = []
    for r in kpi_rows:
        base = float(r["value_scalar"]) or 1.0
        delta = r["delta_prev"]
        if delta is None:
            continue
        delta_f = float(delta)
        if base and abs(delta_f) / (abs(base) + 1e-9) >= 0.10:
            kpis_of_note.append({
                "kpi_id": int(r["kpi_id"]),
                "reason": "delta_gt_10pct",
                "delta": delta_f,
            })

    return {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "tenant": (t["display_name"] if t else None),
        "new_matters": {
            "count": len(new_matters_rows),
            "links": [r["event_id"] for r in new_matters_rows],
        },
        "endorsements_in_flight": {
            "count": len(endorsements_rows),
            "links": [r["event_id"] for r in endorsements_rows],
        },
        "claims_in_flight": {
            "count": len(claims_rows),
            "links": [r["event_id"] for r in claims_rows],
        },
        "hitl_queue_size": int(hitl_size_row["n"]) if hitl_size_row else 0,
        "overdue_hitl": int(overdue_row["n"]) if overdue_row else 0,
        "kpis_of_note": kpis_of_note,
    }
