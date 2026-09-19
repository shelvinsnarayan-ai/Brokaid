"""POST /api/o/compliance/{block,clear/{block_id}} — compliance role only (R5c-4b — Mongo-only)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.sessions import Session
from app.kernel import compliance
from app.kernel.rbac import requires_role

router = APIRouter(prefix="/api/o/compliance")


def _store(request: Request):
    s = getattr(request.app.state, "mongo_db", None)
    if s is None:
        raise HTTPException(status_code=503, detail="mongo_store_unavailable")
    return s


class BlockRequest(BaseModel):
    target_kind: str
    target_id: str
    reason: str


class ClearRequest(BaseModel):
    reason: str


@router.post("/block")
async def block(
    body: BlockRequest,
    request: Request,
    session: Session = Depends(requires_role("compliance")),
) -> dict:
    if body.target_kind not in ("matter", "workflow"):
        raise HTTPException(status_code=400, detail="bad_target_kind")
    block_id = await compliance.add_block(
        _store(request),
        tenant_id=session.tenant_id,
        target_kind=body.target_kind,
        target_id=body.target_id,
        reason=body.reason,
        actor={"kind": "human", "id": session.user_id, "name": "compliance"},
    )
    return {"ok": True, "block_id": block_id}


@router.post("/clear/{block_id}")
async def clear(
    block_id: str,
    body: ClearRequest,
    request: Request,
    session: Session = Depends(requires_role("compliance")),
) -> dict:
    await compliance.clear_block(
        _store(request),
        tenant_id=session.tenant_id,
        block_id=block_id,
        reason=body.reason,
        actor={"kind": "human", "id": session.user_id, "name": "compliance"},
    )
    return {"ok": True}
