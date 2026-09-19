"""Client magic-link auth (R5c-4b — Mongo-only)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, EmailStr

from app.auth import sessions as sess
from app.auth.dispatch import (
    auth_store, create_session_scoped, emit_auth_event,
)
from app.kernel import identity
from app.kernel.tenant_ctx import use_tenant

router = APIRouter(prefix="/api/auth/client")


class MagicLinkRequest(BaseModel):
    email: EmailStr
    brokerage_slug: Optional[str] = None


class SelectRequest(BaseModel):
    tenant_id: str
    client_id: str
    role: str


def _dev_mode() -> bool:
    import os
    return os.environ.get("AUTH_DEV_MODE", "false").lower() == "true"


def _resend_available() -> bool:
    import os
    return bool(os.environ.get("RESEND_API_KEY"))


@router.post("/request")
async def request_magic_link(body: MagicLinkRequest, request: Request) -> dict:
    """Issue a magic-link token. Mongo-only: `brokerage_slug` is REQUIRED
    (tenant is resolved from slug before user lookup — this prevents
    cross-tenant email-existence probes).
    """
    store = auth_store(request)
    dev = _dev_mode()
    resend = _resend_available()
    if not dev and not resend:
        raise HTTPException(status_code=503, detail="email_provider_unavailable")

    if not body.brokerage_slug:
        raise HTTPException(status_code=400, detail="brokerage_slug_required")
    from app.kernel import brokerages_mongo
    brok = await brokerages_mongo.find_brokerage_by_slug_system(
        store, body.brokerage_slug,
    )
    if brok is None:
        return {"ok": True, "dev_mode": dev and not resend}
    tenant_id = brok.tenant_id
    async with use_tenant(tenant_id):
        user = await identity.find_user_by_email(store, body.email)
    if user is None or user.kind != "client":
        return {"ok": True, "dev_mode": dev and not resend}
    from app.kernel import auth_tokens_mongo
    raw, expires = await auth_tokens_mongo.issue_magic_link(
        store, user_id=user.user_id, tenant_id=tenant_id,
    )
    await emit_auth_event(
        store, tenant_id, "MagicLinkIssued",
        actor={"kind": "system", "id": "auth", "name": "magic-link"},
        payload={"user_id": user.user_id,
                 "expires_at": expires.isoformat(),
                 "delivery": "dev-return" if not resend else "resend"},
        idempotency_key=f"ml-issue:{raw[:8]}",
    )

    body_out: dict = {"ok": True, "dev_mode": dev and not resend}
    if dev and not resend:
        body_out["dev_token"] = raw
    return body_out


@router.get("/verify")
async def verify_magic_link(
    request: Request, response: Response, token: str = Query(...)
) -> dict:
    store = auth_store(request)

    from app.kernel import auth_tokens_mongo
    user_id, tenant_id, error = await auth_tokens_mongo.consume_magic_link(
        store, token,
    )
    if error in {"unknown", "already_used"}:
        raise HTTPException(status_code=400, detail=error)
    if error == "expired":
        raise HTTPException(status_code=400, detail="expired")
    assert user_id is not None and tenant_id is not None
    async with use_tenant(tenant_id):
        links = await identity.list_client_links_for_user(store, user_id)
    if not links:
        raise HTTPException(status_code=403, detail="no_client_links")
    first = links[0]
    session = await create_session_scoped(
        store, first.tenant_id,
        user_id=user_id, client_id=first.client_id,
        active_role=first.role, mfa_verified=True,
    )

    response.set_cookie(
        key=sess.COOKIE_NAME, value=session.session_id,
        httponly=True, samesite="lax", secure=False, path="/",
        max_age=12 * 3600,
    )
    await emit_auth_event(
        store, session.tenant_id, "MagicLinkConsumed",
        actor={"kind": "human", "id": session.user_id, "name": session.active_role},
        payload={"user_id": session.user_id, "session_id": session.session_id},
        idempotency_key=f"ml-consume:{session.session_id}",
    )
    return {
        "ok": True,
        "requires_selection": len(links) > 1,
        "links": [
            {"tenant_id": l.tenant_id, "client_id": l.client_id, "role": l.role}
            for l in links
        ],
    }


@router.post("/select")
async def select_link(body: SelectRequest, request: Request) -> dict:
    from app.middleware.session import require_session

    session = await require_session(request)
    store = auth_store(request)

    async with use_tenant(session.tenant_id):
        links = await identity.list_client_links_for_user(store, session.user_id)

    matching = [
        l for l in links
        if l.tenant_id == body.tenant_id
        and l.client_id == body.client_id
        and l.role == body.role
    ]
    if not matching:
        raise HTTPException(status_code=403, detail="no_such_link")
    async with use_tenant(body.tenant_id):
        await identity.update_active_context(
            store, session.session_id,
            tenant_id=body.tenant_id, client_id=body.client_id,
            active_role=body.role,
        )
    return {"ok": True, "active": {
        "tenant_id": matching[0].tenant_id,
        "client_id": matching[0].client_id,
        "role": matching[0].role,
    }}
