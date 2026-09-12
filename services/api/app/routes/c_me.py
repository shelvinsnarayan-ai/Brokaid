"""GET /api/c/me — hydrates the client-app with user + linked client + prefs."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request

from app.auth.sessions import Session
from app.middleware.session import require_session

router = APIRouter(prefix="/api/c")


@router.get("/me")
async def me(request: Request, session: Session = Depends(require_session)) -> dict:
    pool = request.app.state.pool
    async with pool.acquire() as c:
        u = await c.fetchrow(
            "SELECT user_id::text, email, display_name, kind "
            "FROM users WHERE user_id = $1::uuid",
            session.user_id,
        )
        sess_row = await c.fetchrow(
            "SELECT client_prefs FROM sessions WHERE session_id = $1::uuid",
            session.session_id,
        )
        client_row = None
        if session.client_id:
            async with c.transaction():
                await c.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    session.tenant_id,
                )
                client_row = await c.fetchrow(
                    "SELECT client_id::text, display_name, industry, "
                    "       domicile, jurisdiction_code "
                    "FROM clients WHERE client_id = $1::uuid",
                    session.client_id,
                )

    prefs = {}
    if sess_row:
        raw = sess_row["client_prefs"]
        if isinstance(raw, dict):
            prefs = raw
        elif isinstance(raw, str):
            try:
                prefs = json.loads(raw)
            except Exception:
                prefs = {}

    return {
        "user": {
            "user_id": u["user_id"] if u else session.user_id,
            "email": u["email"] if u else None,
            "display_name": u["display_name"] if u else None,
            "kind": u["kind"] if u else None,
        },
        "session": {
            "session_id": session.session_id,
            "active_role": session.active_role,
            "tenant_id": session.tenant_id,
            "client_id": session.client_id,
        },
        "linked_client": (
            {
                "client_id": client_row["client_id"],
                "display_name": client_row["display_name"],
                "industry": client_row["industry"],
                "domicile": client_row["domicile"],
                "jurisdiction_code": client_row["jurisdiction_code"],
            }
            if client_row
            else None
        ),
        "client_prefs": prefs,
    }
