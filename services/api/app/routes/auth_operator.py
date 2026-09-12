"""POST /api/auth/operator/{login,mfa/enrol,mfa/verify}."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr

from app.auth import passwords, sessions as sess, totp as totp_mod
from app.kernel.rbac import LOGIN_CAPABLE_ROLES
from app.kernel.trail import append

router = APIRouter(prefix="/api/auth/operator")


class OperatorLoginRequest(BaseModel):
    email: EmailStr
    password: str


class MfaVerifyRequest(BaseModel):
    code: str


async def _load_operator_by_email(pool, email: str) -> Optional[dict]:
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT user_id, password_hash, kind, mfa_secret "
            "FROM users WHERE email = $1",
            email,
        )
    return dict(row) if row else None


async def _load_login_role_for(pool, user_id: str) -> Optional[tuple[str, str, str]]:
    """Return (tenant_id, brokerage_id, role) — the first login-capable role
    the user holds, or None if the user has no login-capable role.
    """
    async with pool.acquire() as c:
        rows = await c.fetch(
            """
            SELECT ubr.role, b.brokerage_id::text AS brokerage_id,
                   b.tenant_id::text AS tenant_id
              FROM user_brokerage_roles ubr
              JOIN brokerages b ON b.brokerage_id = ubr.brokerage_id
             WHERE ubr.user_id = $1::uuid AND ubr.active = TRUE
            """,
            user_id,
        )
    for r in rows:
        if r["role"] in LOGIN_CAPABLE_ROLES:
            return r["tenant_id"], r["brokerage_id"], r["role"]
    return None


def _set_session_cookie(response: Response, session: sess.Session) -> None:
    response.set_cookie(
        key=sess.COOKIE_NAME,
        value=session.session_id,
        httponly=True,
        samesite="lax",
        secure=False,   # HTTP in dev; production reverse-proxy sets Secure.
        path="/",
        max_age=int((session.expires_at.timestamp()
                     - session.expires_at.timestamp() + 12 * 3600)),
    )


@router.post("/login")
async def login(
    body: OperatorLoginRequest, request: Request, response: Response
) -> dict:
    pool = request.app.state.pool
    user = await _load_operator_by_email(pool, body.email)
    if user is None or user["kind"] != "operator" or user["password_hash"] is None:
        raise HTTPException(status_code=401, detail="invalid_credentials")
    if not passwords.verify_password(user["password_hash"], body.password):
        raise HTTPException(status_code=401, detail="invalid_credentials")

    role_info = await _load_login_role_for(pool, str(user["user_id"]))
    if role_info is None:
        raise HTTPException(status_code=403, detail="no_login_capable_role")
    tenant_id, brokerage_id, role = role_info

    session = await sess.create(
        pool,
        user_id=str(user["user_id"]),
        tenant_id=tenant_id,
        brokerage_id=brokerage_id,
        active_role=role,
        mfa_verified=False,
    )
    _set_session_cookie(response, session)

    try:
        await append(
            pool, tenant_id, "LoginSucceeded",
            actor={"kind": "human", "id": str(user["user_id"]), "name": role},
            payload={"kind": "operator", "session_id": session.session_id,
                     "mfa_verified": False},
            idempotency_key=f"login:op:{session.session_id}",
        )
    except Exception:
        pass

    return {
        "mfa_required": True,
        "enrolment_required": user["mfa_secret"] is None,
        "active_role": role,
    }


@router.post("/mfa/enrol")
async def enrol(request: Request) -> dict:
    from app.middleware.session import require_session

    session = await require_session(request)
    pool = request.app.state.pool

    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT email, kind, mfa_secret FROM users WHERE user_id = $1::uuid",
            session.user_id,
        )
    if row is None or row["kind"] != "operator":
        raise HTTPException(status_code=403, detail="not_operator")
    if row["mfa_secret"] is not None:
        raise HTTPException(status_code=409, detail="already_enrolled")

    secret = totp_mod.new_secret()
    uri = totp_mod.otpauth_uri(secret, account=row["email"])
    svg = totp_mod.qr_svg(uri)

    async with pool.acquire() as c:
        await c.execute(
            "UPDATE users SET mfa_secret = $1 WHERE user_id = $2::uuid",
            secret, session.user_id,
        )

    try:
        await append(
            pool, session.tenant_id, "MfaEnrolled",
            actor={"kind": "human", "id": session.user_id, "name": session.active_role},
            payload={"user_id": session.user_id},
            idempotency_key=f"mfa-enrol:{session.user_id}",
        )
    except Exception:
        pass

    return {"otpauth_uri": uri, "qr_svg": svg}


@router.post("/mfa/verify")
async def verify(
    body: MfaVerifyRequest, request: Request
) -> dict:
    from app.middleware.session import require_session

    session = await require_session(request)
    pool = request.app.state.pool

    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT mfa_secret FROM users WHERE user_id = $1::uuid",
            session.user_id,
        )
    if row is None or row["mfa_secret"] is None:
        raise HTTPException(status_code=409, detail="not_enrolled")
    if not totp_mod.verify_code(row["mfa_secret"], body.code):
        raise HTTPException(status_code=401, detail="invalid_code")

    await sess.mark_mfa_verified(pool, session.session_id)
    try:
        await append(
            pool, session.tenant_id, "MfaVerified",
            actor={"kind": "human", "id": session.user_id, "name": session.active_role},
            payload={"session_id": session.session_id},
            idempotency_key=f"mfa-verify:{session.session_id}",
        )
    except Exception:
        pass
    return {"ok": True}
