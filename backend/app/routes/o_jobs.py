"""R1.1 — Background work panel API (Operator only): counts, leader, dead-letter retry/discard (ledger-recorded)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.sessions import Session
from app.kernel import jobs
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.routes.o_governance import require_operator_role

router = APIRouter(prefix="/api/o/jobs")


@router.get("/summary")
async def summary(request: Request, session: Session = Depends(require_operator_role)) -> dict:
    return await jobs.summary(request.app.state.mongo_db, session.tenant_id)


async def _dead(request: Request, session: Session, job_id: str) -> dict:
    # tenant-safe: system-only — job row scoped to the caller's tenant or system jobs.
    j = await jobs.raw(request.app.state.mongo_db).jobs.find_one(
        {"job_id": job_id, "status": "dead", "tenant_id": {"$in": [session.tenant_id, None]}})
    if not j:
        raise HTTPException(status_code=404, detail="dead_job_not_found")
    return j


async def _record(request: Request, session: Session, type_: str, j: dict) -> None:
    async with use_tenant(session.tenant_id):
        await append_via_dispatch(request.app.state.mongo_db, session.tenant_id, type_,
                                  actor={"kind": "human", "id": session.user_id, "name": session.active_role},
                                  payload={"job_id": j["job_id"], "kind": j["kind"], "attempts": j["attempts"],
                                           "last_error": (j.get("last_error") or "")[:200]},
                                  idempotency_key=f"{type_}:{j['job_id']}:{j['attempts']}:{j.get('finished_at')}")


@router.post("/{job_id}/retry")
async def retry(job_id: str, request: Request, session: Session = Depends(require_operator_role)) -> dict:
    j = await _dead(request, session, job_id)
    now = jobs.now_utc()
    # tenant-safe: system-only — re-queue the dead job with a fresh attempt budget.
    await jobs.raw(request.app.state.mongo_db).jobs.update_one(
        {"job_id": job_id, "status": "dead"},
        {"$set": {"status": "queued", "attempts": 0, "run_after": now, "updated_at": now, "finished_at": None}})
    await _record(request, session, "JobRetried", j)
    return {"job_id": job_id, "status": "queued"}


@router.post("/{job_id}/discard")
async def discard(job_id: str, request: Request, session: Session = Depends(require_operator_role)) -> dict:
    j = await _dead(request, session, job_id)
    # tenant-safe: system-only — mark the dead job discarded (kept for audit).
    await jobs.raw(request.app.state.mongo_db).jobs.update_one(
        {"job_id": job_id, "status": "dead"}, {"$set": {"status": "discarded", "updated_at": jobs.now_utc()}})
    await _record(request, session, "JobDiscarded", j)
    return {"job_id": job_id, "status": "discarded"}
