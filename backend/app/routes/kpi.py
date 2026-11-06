"""Operator KPI routes (R5c-4b — Mongo-only)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth.sessions import Session
from app.kernel import kpis, kpis_dispatch
from app.middleware.session import require_operator_session


router = APIRouter(prefix="/api/o/kpi")


def _store(request: Request):
    s = getattr(request.app.state, "mongo_db", None)
    if s is None:
        raise HTTPException(status_code=503, detail="mongo_store_unavailable")
    return s


@router.get("/summary")
async def summary(
    request: Request,
    session: Session = Depends(require_operator_session),
) -> dict:
    store = _store(request)
    values = await kpis_dispatch.compute_all(store, session.tenant_id)
    out = {"day": datetime.now(timezone.utc).date().isoformat(), "kpis": [v.to_dict() for v in values]}
    from app.kernel.event_text import kpi_people, raw_ok, staff_out
    if raw_ok(session):
        return out
    import copy
    out["kpis"] = await kpi_people(store, session.tenant_id, copy.deepcopy(out["kpis"]))
    return staff_out(session, out)


@router.get("/{kpi_id}/events")
async def drill(
    kpi_id: int, request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(require_operator_session),
) -> dict:
    if kpi_id not in kpis.DRILLABLE:
        raise HTTPException(status_code=400, detail="not_drillable_in_this_build")
    store = _store(request)
    values = await kpis_dispatch.compute_all(store, session.tenant_id)
    target = next((v for v in values if v.kpi_id == kpi_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="kpi_not_found")
    events = list(target.meta.get("events") or [])
    total = len(events)
    page = events[offset: offset + limit]
    from app.kernel.event_text import kpi_people, plain, raw_ok, render, staff_out
    if raw_ok(session):
        await render(store, session.tenant_id, page)
        return {"kpi_id": kpi_id, "label": target.meta.get("label"), "scalar": target.scalar, "count": total, "events": page}
    import copy
    page = copy.deepcopy(page)
    await kpi_people(store, session.tenant_id, [{"meta": {"events": page}}])
    for r in page:  # approvals-load rows carry decision fields, not a ledger event
        if "type" not in r:
            r["type"], r["payload"] = "Approval", {"decision": r.get("decision")}
        if r.get("who"):
            r["actor"] = {"kind": "label", "name": r["who"]}
    await render(store, session.tenant_id, page)
    return staff_out(session, {"kpi_id": kpi_id, "label": target.meta.get("label"), "scalar": target.scalar, "count": total,
                               "events": plain(page)})
