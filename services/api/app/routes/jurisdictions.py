"""GET /api/jurisdictions (list) and /api/jurisdictions/{code}. Public."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.kernel import jurisdiction

router = APIRouter(prefix="/api/jurisdictions")


@router.get("")
async def list_jurisdictions() -> dict:
    return {"jurisdictions": [p.to_dict() for p in jurisdiction.all_packs()]}


@router.get("/{code}")
async def get_jurisdiction(code: str) -> dict:
    try:
        return jurisdiction.get(code).to_dict()
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown_jurisdiction")
