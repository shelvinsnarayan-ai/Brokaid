"""R1.2b fix — one guard for every /api/c route that names an object.

Resolves policy/envelope/certificate/claim/membership/thread ids (path, query or JSON body) to a client_id
and requires that client to be one of the caller's live client links/memberships in the session's tenant.
Otherwise 404 — existence is never revealed, and authority is never evaluated for a foreign client.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant

ID_KEYS = ("policy_id", "envelope_id", "certificate_id", "claim_id", "membership_id", "thread_id", "renewal_id", "doc_id", "invite_id")
NOT_FOUND = HTTPException(status_code=404, detail="not_found")


async def member_clients(safe, user_id: str) -> set[str]:
    links = {r["client_id"] async for r in safe.user_client_links.find({"user_id": user_id})}
    members = {m["client_id"] async for m in safe.memberships.find({"user_id": user_id, "revoked_at": None})}
    return links | members


async def _policy_client(safe, policy_id) -> str | None:
    p = await safe.policies.find_one({"policy_id": policy_id}, {"client_id": 1})
    return p["client_id"] if p else None


async def _event_client(safe, key: str, oid: str) -> str | None:
    """Envelopes and certificates live in the ledger: find an event naming the id with a policy/client."""
    async for e in safe.events.find({f"payload.{key}": oid}, {"payload": 1}).limit(50):
        pl = e.get("payload") or {}
        if pl.get("client_id"):
            return pl["client_id"]
        if pl.get("policy_id"):
            return await _policy_client(safe, pl["policy_id"])
    return None


async def resolve_client(safe, key: str, oid: str) -> str | None:
    if not isinstance(oid, str) or not oid or len(oid) > 100:
        return None
    if key == "policy_id":
        return await _policy_client(safe, oid)
    if key in ("claim_id", "membership_id", "renewal_id"):
        coll = {"claim_id": safe.claims, "membership_id": safe.memberships, "renewal_id": safe.renewals}[key]
        d = await coll.find_one({key: oid}, {"client_id": 1})
        return d["client_id"] if d else None
    if key == "doc_id":  # R3.12 policy documents (schedules)
        d = await safe.policy_documents.find_one({"doc_id": oid}, {"client_id": 1})
        return d["client_id"] if d else None
    if key == "invite_id":  # R3.12 client-issued invites
        from app.kernel.tenant_ctx import get_tenant_id
        d = await safe.system("auth_tokens").find_one({"token_id": oid, "kind": "invite", "tenant_id": get_tenant_id()}, {"client_id": 1})
        return (d or {}).get("client_id")
    if key == "thread_id":  # ask threads are envelopes on ClientQuestionAsked (payload.envelope_id + client_id)
        return await _event_client(safe, "envelope_id", oid)
    return await _event_client(safe, key, oid)


async def _ids(request: Request) -> dict[str, str]:
    ids = {k: v for k, v in request.path_params.items() if k in ID_KEYS}
    ids |= {k: request.query_params[k] for k in ID_KEYS if k in request.query_params}
    if request.method in ("POST", "PUT", "PATCH") and "json" in request.headers.get("content-type", ""):
        try:
            body = await request.json()
        except Exception:
            body = None
        if isinstance(body, dict):
            ids |= {k: body[k] for k in ID_KEYS if isinstance(body.get(k), str) and k not in ids}
    return ids


async def client_object_guard(request: Request) -> None:
    if not request.url.path.startswith("/api/c/"):
        return
    from app.middleware.session import require_session
    try:
        session = await require_session(request)
    except HTTPException:
        return  # unauthenticated: the route's own dependency answers 401
    if session.active_role == "platform_admin":
        raise NOT_FOUND  # platform admins have no client surface at all
    ids = await _ids(request)
    if not ids:
        return
    from app.kernel.rbac import ALL_OPERATOR_ROLES
    if session.active_role in ALL_OPERATOR_ROLES:
        raise HTTPException(status_code=403, detail="client_only")  # staff never use client routes
    safe = wrap(request.app.state.mongo_db)
    async with use_tenant(session.tenant_id):
        allowed = await member_clients(safe, session.user_id)
        for key, oid in ids.items():
            cid = await resolve_client(safe, key, oid)
            if cid is None or cid not in allowed:
                raise NOT_FOUND
