"""R0.5 — operator governance: kill switches, spend estimate, demo reset."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.sessions import Session
from app.kernel import demo_reset, governance, spend
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.middleware.session import require_operator_session

router = APIRouter(prefix="/api/o")


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


@router.get("/kill-switch")
async def list_switches(request: Request, session: Session = Depends(require_operator_session)) -> dict:
    rows = await governance.list_all(_db(request))
    return {"switches": [_view(r) for r in rows],
            "agents": [{"ref": k, "label": v, "package": governance.AGENT_PACKAGES[k]}
                       for k, v in governance.AGENT_LABELS.items()],
            "packages": [{"ref": k, "label": v} for k, v in governance.PACKAGE_LABELS.items()],
            "cache_ttl_s": governance.CACHE_TTL_S}


@router.post("/kill-switch/engage")
async def engage(body: EngageBody, request: Request,
                 session: Session = Depends(require_operator_session)) -> dict:
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
                  session: Session = Depends(require_operator_session)) -> dict:
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
                session: Session = Depends(require_operator_session)) -> dict:
    """Same cached check Brain.invoke uses — for the drill script."""
    row = await governance.check(_db(request), agent, tenant_id or session.tenant_id)
    return {"agent": agent, "blocked": row is not None, "switch_id": (row or {}).get("switch_id")}


@router.get("/spend")
async def get_spend(request: Request, session: Session = Depends(require_operator_session)) -> dict:
    return await spend.summary(_db(request), session.tenant_id)


@router.post("/demo/reset")
async def reset(body: ResetBody, request: Request,
                session: Session = Depends(require_operator_session)) -> dict:
    return await demo_reset.run(_db(request), session.tenant_id, body.scope, session.user_id)
