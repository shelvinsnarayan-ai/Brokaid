"""POST /api/auth/operator/{login,mfa/enrol,mfa/verify} (R5c-4b — Mongo-only)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr

from app.auth import passwords, sessions as sess, totp as totp_mod
from app.auth.dispatch import (
    auth_store, create_session_scoped, emit_auth_event,
    set_mfa_secret_scoped, user_by_email_pre_auth,
)
from app.kernel import identity
from app.kernel.rbac import LOGIN_CAPABLE_ROLES
from app.kernel.tenant_ctx import use_tenant

router = APIRouter(prefix="/api/auth/operator")


class OperatorLoginRequest(BaseModel):
    email: EmailStr
    password: str


class MfaVerifyRequest(BaseModel):
    code: str


async def _find_login_role(store, user_id: str):
    """Return (tenant_id, brokerage_id, role) for the first login-capable
    brokerage role the user holds, or None."""
    for r in await identity.list_brokerage_roles_across_tenants(store, user_id):
        if r.active and r.role in LOGIN_CAPABLE_ROLES:
            return r.tenant_id, r.brokerage_id, r.role
    return None


def _set_session_cookie(response: Response, session: sess.Session) -> None:
    response.set_cookie(
        key=sess.COOKIE_NAME, value=session.session_id,
        httponly=True, samesite="lax", secure=False, path="/",
        max_age=12 * 3600,
    )


@router.post("/login")
async def login(
    body: OperatorLoginRequest, request: Request, response: Response
) -> dict:
    store = auth_store(request)
    user, _tenant_hint = await user_by_email_pre_auth(store, body.email)
    if user is None or user.kind != "operator" or user.password_hash is None:
        raise HTTPException(status_code=401, detail="invalid_credentials")
    if not passwords.verify_password(user.password_hash, body.password):
        raise HTTPException(status_code=401, detail="invalid_credentials")

    role_info = await _find_login_role(store, user.user_id)
    if role_info is None:
        raise HTTPException(status_code=403, detail="no_login_capable_role")
    tenant_id, brokerage_id, role = role_info

    session = await create_session_scoped(
        store, tenant_id,
        user_id=user.user_id, brokerage_id=brokerage_id,
        active_role=role, mfa_verified=False,
    )
    _set_session_cookie(response, session)

    await emit_auth_event(
        store, tenant_id, "LoginSucceeded",
        actor={"kind": "human", "id": user.user_id, "name": role},
        payload={"kind": "operator", "session_id": session.session_id,
                 "mfa_verified": False},
        idempotency_key=f"login:op:{session.session_id}",
    )
    return {
        "mfa_required": True,
        "enrolment_required": user.mfa_secret is None,
        "active_role": role,
    }


@router.post("/mfa/enrol")
async def enrol(request: Request) -> dict:
    from app.middleware.session import require_session

    session = await require_session(request)
    store = auth_store(request)

    async with use_tenant(session.tenant_id):
        user = await identity.find_user_by_id(store, session.user_id)

    if user is None or user.kind != "operator":
        raise HTTPException(status_code=403, detail="not_operator")
    if user.mfa_secret is not None:
        raise HTTPException(status_code=409, detail="already_enrolled")

    secret = totp_mod.new_secret()
    uri = totp_mod.otpauth_uri(secret, account=user.email)
    svg = totp_mod.qr_svg(uri)

    await set_mfa_secret_scoped(store, session.tenant_id, session.user_id, secret)
    await emit_auth_event(
        store, session.tenant_id, "MfaEnrolled",
        actor={"kind": "human", "id": session.user_id, "name": session.active_role},
        payload={"user_id": session.user_id},
        idempotency_key=f"mfa-enrol:{session.user_id}",
    )
    return {"otpauth_uri": uri, "qr_svg": svg}


@router.post("/mfa/verify")
async def verify(
    body: MfaVerifyRequest, request: Request
) -> dict:
    from app.middleware.session import require_session

    session = await require_session(request)
    store = auth_store(request)

    async with use_tenant(session.tenant_id):
        user = await identity.find_user_by_id(store, session.user_id)

    if user is None or user.mfa_secret is None:
        raise HTTPException(status_code=409, detail="not_enrolled")
    if not totp_mod.verify_code(user.mfa_secret, body.code):
        raise HTTPException(status_code=401, detail="invalid_code")

    async with use_tenant(session.tenant_id):
        await identity.mark_mfa_verified(store, session.session_id)

    await emit_auth_event(
        store, session.tenant_id, "MfaVerified",
        actor={"kind": "human", "id": session.user_id, "name": session.active_role},
        payload={"session_id": session.session_id},
        idempotency_key=f"mfa-verify:{session.session_id}",
    )
    return {"ok": True}
