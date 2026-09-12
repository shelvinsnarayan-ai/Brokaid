"""POST /api/c/preferences — client-app dev-mode preference store."""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.sessions import Session
from app.middleware.session import require_session

router = APIRouter(prefix="/api/c")


class PrefsRequest(BaseModel):
    jurisdiction: str | None = None


@router.post("/preferences")
async def set_prefs(
    body: PrefsRequest, request: Request,
    session: Session = Depends(require_session),
) -> dict:
    # In production the client's jurisdiction is authoritative; the
    # override lives only in dev-mode preview.
    if body.jurisdiction is not None and body.jurisdiction not in ("AU", "SG"):
        raise HTTPException(status_code=400, detail="unknown_jurisdiction")

    if (body.jurisdiction is not None
            and os.environ.get("AUTH_DEV_MODE", "false").lower() != "true"):
        raise HTTPException(status_code=403, detail="preview_only_in_dev_mode")

    pool = request.app.state.pool
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT client_prefs FROM sessions WHERE session_id = $1::uuid",
            session.session_id,
        )
        current = row["client_prefs"] if row else {}
        if isinstance(current, str):
            try:
                current = json.loads(current)
            except Exception:
                current = {}
        elif current is None:
            current = {}
        if body.jurisdiction is None:
            current.pop("jurisdiction", None)
        else:
            current["jurisdiction"] = body.jurisdiction
        await c.execute(
            "UPDATE sessions SET client_prefs = $1::jsonb WHERE session_id = $2::uuid",
            json.dumps(current), session.session_id,
        )
    return {"ok": True, "client_prefs": current}
