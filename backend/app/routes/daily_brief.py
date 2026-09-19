"""Operator Daily Brief — projection-only, Mongo-only (R5c-4b)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.sessions import Session
from app.kernel import daily_brief as daily_brief_kernel
from app.kernel import kpis_dispatch
from app.middleware.session import require_operator_session


router = APIRouter(prefix="/api/o")


def _store(request: Request):
    s = getattr(request.app.state, "mongo_db", None)
    if s is None:
        raise HTTPException(status_code=503, detail="mongo_store_unavailable")
    return s


@router.get("/daily_brief")
async def daily_brief(
    request: Request,
    session: Session = Depends(require_operator_session),
) -> dict[str, Any]:
    store = _store(request)
    body = await daily_brief_kernel.compose_brief_body(store, session.tenant_id)

    values = await kpis_dispatch.compute_all(store, session.tenant_id)
    kpi_rows = [
        {"kpi_id": v.kpi_id, "value_scalar": v.scalar,
         "delta_prev": v.delta_prev}
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
        "tenant": body["tenant_display"],
        "new_matters": body["new_matters"],
        "endorsements_in_flight": body["endorsements_in_flight"],
        "claims_in_flight": body["claims_in_flight"],
        "hitl_queue_size": body["hitl_queue_size"],
        "overdue_hitl": body["overdue_hitl"],
        "kpis_of_note": kpis_of_note,
    }
