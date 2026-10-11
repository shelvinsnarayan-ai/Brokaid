"""GET /api/c/me — Mongo-only hydration (R5c-4b)."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.kernel.authority import memberships_for_user
from app.auth.sessions import Session
from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_session


router = APIRouter(prefix="/api/c")


def _parse_prefs(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


async def _hydrate(store, session: Session) -> dict[str, Any]:
    safe = wrap(store)
    async with use_tenant(session.tenant_id):
        u = await safe.users.find_one({"user_id": session.user_id})
    s = await safe.system("sessions").find_one({"session_id": session.session_id})
    client_row = None
    if session.client_id:
        async with use_tenant(session.tenant_id):
            client_row = await safe.clients.find_one(
                {"client_id": session.client_id}
            )
    prefs = _parse_prefs((s or {}).get("client_prefs") or {})
    return {
        "user": (
            {"user_id": u["user_id"], "email": u.get("email"),
             "display_name": u.get("display_name"), "kind": u.get("kind")}
            if u else
            {"user_id": session.user_id, "email": None,
             "display_name": None, "kind": None}
        ),
        "linked_client": (
            {"client_id": client_row["client_id"],
             "display_name": client_row.get("display_name"),
             "industry": client_row.get("industry"),
             "domicile": client_row.get("domicile"),
             "jurisdiction_code": client_row.get("jurisdiction_code")}
            if client_row else None
        ),
        "client_prefs": prefs,
    }


@router.get("/me")
async def me(request: Request, session: Session = Depends(require_session)) -> dict:
    body = await _hydrate(request.app.state.mongo_db, session)
    return {
        "user": body["user"],
        "session": {
            "session_id": session.session_id,
            "active_role": session.active_role,
            "tenant_id": session.tenant_id,
            "client_id": session.client_id,
        },
        "linked_client": body["linked_client"],
        "client_prefs": body["client_prefs"],
        "memberships": await memberships_for_user(
            request.app.state.mongo_db, session.tenant_id, session.user_id,
        ),
    }
