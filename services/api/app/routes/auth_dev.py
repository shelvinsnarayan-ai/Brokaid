"""/*! brokaid:dev-only */
Dev-only impersonation. Mounted only when `AUTH_DEV_MODE=true`.

Picks a seeded user by email and hands the browser a session cookie
with MFA already flagged as verified so tests and manual walk-throughs
can skip the full login flow.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr

from app.auth import sessions as sess
from app.kernel.rbac import LOGIN_CAPABLE_ROLES

router = APIRouter(prefix="/api/auth/dev")


class ImpersonateRequest(BaseModel):
    email: EmailStr


@router.post("/impersonate")
async def impersonate(
    body: ImpersonateRequest, request: Request, response: Response
) -> dict:
    """Dev impersonation. Body: {email}. Returns session context."""
    pool = request.app.state.pool
    async with pool.acquire() as c:
        user = await c.fetchrow(
            "SELECT user_id, kind FROM users WHERE email = $1", body.email
        )
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")

    if user["kind"] == "operator":
        async with pool.acquire() as c:
            rows = await c.fetch(
                """
                SELECT ubr.role, b.brokerage_id::text AS brokerage_id,
                       b.tenant_id::text AS tenant_id
                  FROM user_brokerage_roles ubr
                  JOIN brokerages b ON b.brokerage_id = ubr.brokerage_id
                 WHERE ubr.user_id = $1::uuid AND ubr.active = TRUE
                """,
                str(user["user_id"]),
            )
        row = next(
            (r for r in rows if r["role"] in LOGIN_CAPABLE_ROLES), None
        )
        if row is None:
            raise HTTPException(status_code=403, detail="no_login_capable_role")
        session = await sess.create(
            pool,
            user_id=str(user["user_id"]),
            tenant_id=row["tenant_id"],
            brokerage_id=row["brokerage_id"],
            active_role=row["role"],
            mfa_verified=True,   # dev-only bypass
        )
    else:
        async with pool.acquire() as c:
            row = await c.fetchrow(
                """
                SELECT tenant_id::text AS tenant_id, client_id::text AS client_id, role
                  FROM user_client_links WHERE user_id = $1::uuid LIMIT 1
                """,
                str(user["user_id"]),
            )
        if row is None:
            raise HTTPException(status_code=403, detail="no_client_link")
        session = await sess.create(
            pool,
            user_id=str(user["user_id"]),
            tenant_id=row["tenant_id"],
            client_id=row["client_id"],
            active_role=row["role"],
            mfa_verified=True,
        )

    response.set_cookie(
        key=sess.COOKIE_NAME, value=session.session_id,
        httponly=True, samesite="lax", secure=False, path="/",
        max_age=12 * 3600,
    )
    return {
        "session_id": session.session_id,
        "tenant_id": session.tenant_id,
        "active_role": session.active_role,
        "user_kind": user["kind"],
    }
