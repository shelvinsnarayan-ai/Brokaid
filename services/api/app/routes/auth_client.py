"""Client magic-link auth."""
from __future__ import annotations

import os
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, EmailStr

from app.auth import magic_link, sessions as sess
from app.kernel.trail import append

router = APIRouter(prefix="/api/auth/client")


class MagicLinkRequest(BaseModel):
    email: EmailStr


class SelectRequest(BaseModel):
    tenant_id: str
    client_id: str
    role: str


def _dev_mode() -> bool:
    return os.environ.get("AUTH_DEV_MODE", "false").lower() == "true"


def _resend_available() -> bool:
    return bool(os.environ.get("RESEND_API_KEY"))


async def _load_client_user(pool, email: str) -> Optional[dict]:
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT user_id, kind FROM users WHERE email = $1", email
        )
    return dict(row) if row else None


async def _links_for(pool, user_id: str) -> list[dict]:
    async with pool.acquire() as c:
        rows = await c.fetch(
            """
            SELECT tenant_id::text AS tenant_id,
                   client_id::text AS client_id,
                   role
              FROM user_client_links
             WHERE user_id = $1::uuid
            """,
            user_id,
        )
    return [dict(r) for r in rows]


@router.post("/request")
async def request_magic_link(body: MagicLinkRequest, request: Request) -> dict:
    """Issue a magic-link token. Response body includes the token in dev
    mode when Resend is not configured — this is the only path a demo
    can take without a real email provider.
    """
    pool = request.app.state.pool
    user = await _load_client_user(pool, body.email)
    if user is None or user["kind"] != "client":
        # Reveal nothing about existence to the caller; still return 200
        # so probing doesn't enumerate emails.
        return {"ok": True, "dev_mode": False}

    dev = _dev_mode()
    resend = _resend_available()
    if not dev and not resend:
        raise HTTPException(
            status_code=503, detail="email_provider_unavailable"
        )

    raw, expires = await magic_link.issue(pool, str(user["user_id"]))

    # Emit a ledger event under the first tenant this user is linked
    # to (client identity spans tenants; the event needs a tenant).
    links = await _links_for(pool, str(user["user_id"]))
    if links:
        try:
            await append(
                pool, links[0]["tenant_id"], "MagicLinkIssued",
                actor={"kind": "system", "id": "auth", "name": "magic-link"},
                payload={
                    "user_id": str(user["user_id"]),
                    "expires_at": expires.isoformat(),
                    "delivery": "dev-return" if not resend else "resend",
                },
                idempotency_key=f"ml-issue:{raw[:8]}",
            )
        except Exception:
            pass

    body_out: dict = {"ok": True, "dev_mode": dev and not resend}
    if dev and not resend:
        body_out["dev_token"] = raw
    return body_out


@router.get("/verify")
async def verify_magic_link(
    request: Request, response: Response, token: str = Query(...)
) -> dict:
    pool = request.app.state.pool
    user_id, error = await magic_link.consume(pool, token)
    if error == "unknown" or error == "already_used":
        raise HTTPException(status_code=400, detail=error)
    if error == "expired":
        raise HTTPException(status_code=400, detail="expired")
    assert user_id is not None

    links = await _links_for(pool, user_id)
    if not links:
        raise HTTPException(status_code=403, detail="no_client_links")

    first = links[0]
    session = await sess.create(
        pool,
        user_id=user_id,
        tenant_id=first["tenant_id"],
        client_id=first["client_id"],
        active_role=first["role"],
        mfa_verified=True,   # magic-link IS the second factor for clients
    )
    response.set_cookie(
        key=sess.COOKIE_NAME, value=session.session_id,
        httponly=True, samesite="lax", secure=False, path="/",
        max_age=12 * 3600,
    )
    try:
        await append(
            pool, first["tenant_id"], "MagicLinkConsumed",
            actor={"kind": "human", "id": user_id, "name": first["role"]},
            payload={"user_id": user_id, "session_id": session.session_id},
            idempotency_key=f"ml-consume:{session.session_id}",
        )
    except Exception:
        pass

    return {
        "ok": True,
        "requires_selection": len(links) > 1,
        "links": links,
    }


@router.post("/select")
async def select_link(body: SelectRequest, request: Request) -> dict:
    from app.middleware.session import require_session

    session = await require_session(request)
    pool = request.app.state.pool
    links = await _links_for(pool, session.user_id)
    matching = [
        l for l in links
        if l["tenant_id"] == body.tenant_id
        and l["client_id"] == body.client_id
        and l["role"] == body.role
    ]
    if not matching:
        raise HTTPException(status_code=403, detail="no_such_link")
    await sess.update_active_context(
        pool, session.session_id,
        tenant_id=body.tenant_id, client_id=body.client_id,
        active_role=body.role,
    )
    return {"ok": True, "active": matching[0]}
