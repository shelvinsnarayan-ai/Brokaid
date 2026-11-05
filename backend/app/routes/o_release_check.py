"""R1.0 — POST /api/o/release-check (async run), GET /api/o/release-check/latest | /{run_id}. Operator only."""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel import release_check as rc
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.routes.o_governance import require_operator_role

router = APIRouter(prefix="/api/o/release-check")
log = logging.getLogger("brokaid.release_check")
_TASKS: set = set()


class StartBody(BaseModel):
    inject: str | None = None  # non-production only: force one step to fail


def _view(r: dict | None) -> dict | None:
    if not r:
        return None
    iso = lambda v: v.replace(tzinfo=v.tzinfo or timezone.utc).isoformat() if isinstance(v, datetime) else v  # noqa: E731
    out = {k: iso(v) for k, v in r.items() if k not in ("_id", "tenant_id")}
    out["verified"] = r.get("result") == "PASS" and r.get("env") == "production"
    return out


async def _finish(app, store, tenant_id: str, slug: str, run_id: str, session: Session, inject: str | None) -> None:
    safe, started = wrap(store), datetime.now(timezone.utc)
    try:
        res = await rc.execute(app, store, tenant_id, slug, run_id, inject)
    except Exception as e:  # noqa: BLE001
        log.exception("release check crashed")
        res = {"sha": None, "env": rc.environment(), "result": "FAIL",
               "checks": [{"key": "run", "label": "Release check", "status": "fail", "ms": 0, "detail": f"Crashed: {e!r}"[:200]}]}
    finally:
        await rc.release_lock(store, run_id)
    done = datetime.now(timezone.utc)
    failed = next((c for c in res["checks"] if c["status"] == "fail"), None)
    async with use_tenant(tenant_id):
        await safe.release_check_runs.update_one({"run_id": run_id}, {"$set": {
            **res, "status": "done", "finished_at": done, "duration_ms": round((done - started).total_seconds() * 1000),
            "failed_step": failed and {"label": failed["label"], "detail": failed["detail"]}}})
        await append_via_dispatch(store, tenant_id, "ReleaseCheckCompleted",
                                  actor={"kind": "human", "id": session.user_id, "name": session.active_role},
                                  payload={"run_id": run_id, "sha": res.get("sha"), "env": res["env"], "result": res["result"],
                                           "failed_step": failed["key"] if failed else None},
                                  idempotency_key=f"release_check:{run_id}")


@router.post("")
async def start(request: Request, body: StartBody = Body(default_factory=StartBody),
                session: Session = Depends(require_operator_role)) -> dict:
    if body.inject and rc.environment() == "production":
        raise HTTPException(status_code=422, detail="inject_not_allowed_in_production")
    store = request.app.state.mongo_db
    # tenant-safe: system-only — slug of the caller's own tenant.
    t = await store.tenants.find_one({"tenant_id": session.tenant_id})
    if not t or t.get("slug") != "harbourline":
        raise HTTPException(status_code=422, detail="release_check_runs_in_harbourline_only")
    run_id = str(uuid.uuid4())
    if not await rc.acquire_lock(store, run_id):
        raise HTTPException(status_code=409, detail="release_check_already_running")
    async with use_tenant(session.tenant_id):
        await wrap(store).release_check_runs.insert_one({
            "run_id": run_id, "status": "running", "started_at": datetime.now(timezone.utc), "actor": session.user_id,
            "env": rc.environment(), "inject": body.inject, "result": None, "checks": []})
    from app.kernel import devmode
    rc.SERVED_HOST.set(devmode.served_host(request))  # R3.10: copied into the task's context
    task = asyncio.create_task(_finish(request.app, store, session.tenant_id, t["slug"], run_id, session, body.inject))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {"run_id": run_id, "status": "running"}


@router.get("/latest")
async def latest(request: Request, session: Session = Depends(require_operator_role)) -> dict:
    async with use_tenant(session.tenant_id):
        rows = [r async for r in wrap(request.app.state.mongo_db).release_check_runs.find({}).sort("started_at", -1).limit(10)]
    return {"latest": _view(rows[0]) if rows else None, "history": [_view(r) for r in rows],
            "environment": rc.environment(), "inject_allowed": rc.environment() != "production",
            "app_base_url": os.environ.get("APP_BASE_URL")}


@router.get("/{run_id}")
async def get_run(run_id: str, request: Request, session: Session = Depends(require_operator_role)) -> dict:
    async with use_tenant(session.tenant_id):
        r = await wrap(request.app.state.mongo_db).release_check_runs.find_one({"run_id": run_id})
    if not r:
        raise HTTPException(status_code=404, detail="run_not_found")
    return _view(r)
