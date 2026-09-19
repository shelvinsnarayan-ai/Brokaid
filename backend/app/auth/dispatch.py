"""R5c-4b — auth-route helpers (Mongo-only)."""
from __future__ import annotations

from fastapi import HTTPException, Request

from app.kernel import identity
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch


def auth_store(request: Request):
    """Return the Mongo store attached to `app.state.mongo_db`."""
    store = getattr(request.app.state, "mongo_db", None)
    if store is None:
        raise HTTPException(status_code=503, detail="mongo_store_unavailable")
    return store


async def emit_auth_event(
    store, tenant_id: str, type: str, *,
    actor: dict, payload: dict, idempotency_key: str,
) -> None:
    """Fire an auth-related ledger event. Swallows errors: the
    session cookie has already been set by the time we get here."""
    try:
        async with use_tenant(tenant_id):
            await append_via_dispatch(
                store, tenant_id, type,
                actor=actor, payload=payload,
                idempotency_key=idempotency_key,
            )
    except Exception:
        pass


async def create_session_scoped(store, tenant_id: str, **kwargs):
    """Create a session under `use_tenant(tenant_id)`."""
    async with use_tenant(tenant_id):
        return await identity.create_session(
            store, tenant_id=tenant_id, **kwargs,
        )


async def set_mfa_secret_scoped(store, tenant_id: str, user_id: str, secret: str):
    async with use_tenant(tenant_id):
        await identity.set_mfa_secret(store, user_id, secret)


async def user_by_email_pre_auth(store, email: str):
    """Pre-auth email lookup — returns (User, tenant_id) or (None, None)."""
    return await identity.find_user_by_email_across_tenants(store, email)
