"""R0.5 — operator governance: kill switches, spend estimate, demo reset."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.sessions import Session
from app.kernel import demo_reset, governance, registry, spend
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.middleware.session import require_operator_session

router = APIRouter(prefix="/api/o")


def require_operator_role(session: Session = Depends(require_operator_session)) -> Session:
    """R0.6: agent operations are Operator-only (role=operator; not principal)."""
    if session.active_role != "operator":
        raise HTTPException(status_code=403, detail="operator_role_required")
    return session


def _db(request: Request):
    return request.app.state.mongo_db


def _actor(session: Session) -> dict:
    return {"kind": "human", "id": session.user_id, "name": session.active_role}


async def _event(request, session, type_, payload, key):
    async with use_tenant(session.tenant_id):
        await append_via_dispatch(_db(request), session.tenant_id, type_, actor=_actor(session),
                                  payload=payload, idempotency_key=key)


class EngageBody(BaseModel):
    scope_kind: Literal["agent", "package", "global"]
    scope_ref: str | None = None
    tenant_id: str | None = None
    reason: str = Field(min_length=3)


class ReleaseBody(BaseModel):
    switch_id: str
    reason: str = Field(min_length=3)


class ResetBody(BaseModel):
    scope: Literal["kai", "all_tests", "full"]


def _view(row: dict) -> dict:
    ref = row.get("scope_ref")
    label = {"agent": governance.AGENT_LABELS, "package": governance.PACKAGE_LABELS}.get(
        row["scope_kind"], {}).get(ref, "Everything")
    return {**row, "scope_label": label,
            **{k: (row[k].isoformat() if hasattr(row.get(k), "isoformat") else row.get(k))
               for k in ("enabled_at", "released_at")}}


@router.get("/governance/go-live")
async def go_live(request: Request, session: Session = Depends(require_operator_session)) -> dict:
    """R1.9: the tenant's own go-live checklist (same items the platform admin sees)."""
    from app.kernel import platform
    store = request.app.state.mongo_db
    t = await store.tenants.find_one({"tenant_id": session.tenant_id}, {"status": 1})  # tenant-safe: system-only — own row
    items = await platform.checklist(store, session.tenant_id)
    return {"status": (t or {}).get("status") or "live", "checklist": items, "missing": [i["label"] for i in items if not i["done"]]}


@router.get("/kill-switch")
async def list_switches(request: Request, session: Session = Depends(require_operator_role)) -> dict:
    rows = await governance.list_all(_db(request))
    return {"switches": [_view(r) for r in rows],
            "agents": [{"ref": k, "label": v, "package": governance.AGENT_PACKAGES[k]}
                       for k, v in governance.AGENT_LABELS.items()],
            "packages": [{"ref": k, "label": v} for k, v in governance.PACKAGE_LABELS.items()],
            "cache_ttl_s": governance.CACHE_TTL_S}


@router.post("/kill-switch/engage")
async def engage(body: EngageBody, request: Request,
                 session: Session = Depends(require_operator_role)) -> dict:
    if body.scope_kind == "agent" and body.scope_ref not in governance.AGENT_PACKAGES:
        raise HTTPException(status_code=422, detail="unknown_agent")
    if body.scope_kind == "package" and body.scope_ref not in governance.PACKAGE_LABELS:
        raise HTTPException(status_code=422, detail="unknown_package")
    row = await governance.engage(_db(request), scope_kind=body.scope_kind, scope_ref=body.scope_ref,
                                  tenant_id=body.tenant_id, reason=body.reason, user_id=session.user_id)
    await _event(request, session, "KillSwitchEngaged",
                 {k: row[k] for k in ("switch_id", "scope_kind", "scope_ref", "tenant_id", "reason")},
                 f"killswitch:engage:{row['switch_id']}")
    return {"switch": _view(row)}


@router.post("/kill-switch/release")
async def release(body: ReleaseBody, request: Request,
                  session: Session = Depends(require_operator_role)) -> dict:
    row = await governance.release(_db(request), switch_id=body.switch_id, reason=body.reason,
                                   user_id=session.user_id)
    if not row:
        raise HTTPException(status_code=404, detail="switch_not_found_or_already_released")
    await _event(request, session, "KillSwitchReleased",
                 {"switch_id": body.switch_id, "reason": body.reason},
                 f"killswitch:release:{body.switch_id}")
    return {"switch": _view(row)}


@router.get("/kill-switch/check")
async def check(agent: str, request: Request, tenant_id: str | None = None,
                session: Session = Depends(require_operator_role)) -> dict:
    """Same cached check Brain.invoke uses — for the drill script."""
    row = await governance.check(_db(request), agent, tenant_id or session.tenant_id)
    return {"agent": agent, "blocked": row is not None, "switch_id": (row or {}).get("switch_id")}


@router.get("/registry")
async def get_registry(request: Request, session: Session = Depends(require_operator_role)) -> dict:
    return {"agents": registry.listing(), "autonomy_levels": registry.AUTONOMY,
            "counts": await registry.counts(_db(request), session.tenant_id)}


@router.get("/spend")
async def get_spend(request: Request, session: Session = Depends(require_operator_role)) -> dict:
    return await spend.summary(_db(request), session.tenant_id)


@router.post("/demo/reset")
async def reset(body: ResetBody, request: Request,
                session: Session = Depends(require_operator_role)) -> dict:
    return await demo_reset.run(_db(request), session.tenant_id, body.scope, session.user_id)


class DrillBody(BaseModel):
    agent: str
    engage_s: float
    release_s: float
    result: Literal["pass", "fail"]
    engaged_at: str
    released_at: str


@router.post("/governance/drills")
async def record_drill(body: DrillBody, request: Request, session: Session = Depends(require_operator_role)) -> dict:
    """R0.7 — kill-switch drill evidence (Gate 0 G6 artefact)."""
    import uuid
    pkg = next((r["package"] for r in registry.listing()
                if r["assistant"] == governance.AGENT_LABELS.get(body.agent)), None)
    drill = {"drill_id": str(uuid.uuid4()), **body.model_dump(),
             "agent_label": governance.AGENT_LABELS.get(body.agent, body.agent),
             "package_label": pkg, "operator_id": session.user_id}
    await _event(request, session, "DrillRecorded", drill, f"drill:{drill['drill_id']}")
    return {"drill": drill}


@router.get("/governance/drills")
async def list_drills(request: Request, session: Session = Depends(require_operator_role)) -> dict:
    from app.db.tenant_safe import wrap
    safe = wrap(_db(request))
    async with use_tenant(session.tenant_id):
        evs = [e async for e in safe.events.find({"type": "DrillRecorded"}).sort("seq", -1).limit(10)]
        uids = list({(e["payload"] or {}).get("operator_id") for e in evs})
        names = {u["user_id"]: u.get("display_name") or u["email"]
                 async for u in safe.users.find({"user_id": {"$in": uids}})}
    out = []
    for e in evs:
        p = e["payload"]
        out.append({k: p.get(k) for k in ("drill_id", "agent_label", "package_label", "engage_s",
                                          "release_s", "result", "engaged_at", "released_at")}
                   | {"operator": names.get(p.get("operator_id"), "Operator"),
                      "recorded_at": e["ts"].isoformat() if hasattr(e["ts"], "isoformat") else str(e["ts"])})
    return {"drills": out}


@router.get("/exposure-report")
async def exposure_report(request: Request, since: str | None = None, session: Session = Depends(require_operator_role)) -> dict:
    """R3.10: read-only. Dev impersonation sessions, demo resets/renewals, MFA enrolments and demo logins since `since` (ISO; default 30 days)."""
    from datetime import datetime, timedelta, timezone

    from app.kernel import exposure
    try:
        t = datetime.fromisoformat(since.replace("Z", "+00:00")) if since else datetime.now(timezone.utc) - timedelta(days=30)
    except ValueError as e:
        raise HTTPException(status_code=422, detail="since must be an ISO date/time") from e
    return await exposure.build(request.app.state.mongo_db, t if t.tzinfo else t.replace(tzinfo=timezone.utc))
