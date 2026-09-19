"""POST /api/c/preferences — Mongo-only (R5c-4b)."""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.middleware.session import require_session


router = APIRouter(prefix="/api/c")


class PrefsRequest(BaseModel):
    jurisdiction: str | None = None


def _parse(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


async def _read_prefs(store, session_id: str) -> dict:
    safe = wrap(store)
    row = await safe.system("sessions").find_one({"session_id": session_id})
    return _parse((row or {}).get("client_prefs") or {})


async def _apply(store, session_id: str, current: dict) -> None:
    safe = wrap(store)
    await safe.system("sessions").update_one(
        {"session_id": session_id},
        {"$set": {"client_prefs": current}},
    )


@router.post("/preferences")
async def set_prefs(
    body: PrefsRequest, request: Request,
    session: Session = Depends(require_session),
) -> dict:
    if body.jurisdiction is not None and body.jurisdiction not in ("AU", "SG"):
        raise HTTPException(status_code=400, detail="unknown_jurisdiction")
    if (body.jurisdiction is not None
            and os.environ.get("AUTH_DEV_MODE", "false").lower() != "true"):
        raise HTTPException(status_code=403, detail="preview_only_in_dev_mode")

    store = request.app.state.mongo_db
    current = await _read_prefs(store, session.session_id)
    if body.jurisdiction is None:
        current.pop("jurisdiction", None)
    else:
        current["jurisdiction"] = body.jurisdiction
    await _apply(store, session.session_id, current)
    return {"ok": True, "client_prefs": current}
