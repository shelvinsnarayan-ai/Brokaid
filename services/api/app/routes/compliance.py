"""POST /api/o/compliance/{block,clear/{block_id}} — compliance role only."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.sessions import Session
from app.kernel import compliance
from app.kernel.rbac import requires_role

router = APIRouter(prefix="/api/o/compliance")


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
    pool = request.app.state.pool
    block_id = await compliance.add_block(
        pool,
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
    pool = request.app.state.pool
    await compliance.clear_block(
        pool,
        tenant_id=session.tenant_id,
        block_id=block_id,
        reason=body.reason,
        actor={"kind": "human", "id": session.user_id, "name": "compliance"},
    )
    return {"ok": True}
