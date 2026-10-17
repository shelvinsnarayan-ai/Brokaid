"""R0.4 — client memberships + authority checks (A02 tests 3, 4, 6).

`memberships` carries per-client authority. `authority_events` is the audit
trail: every approve/pay/sign attempt writes one row, allowed or denied.
Additive to the operator gate — never replaces OperatorApproval.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant

ALL = {"can_approve": True, "can_pay": True, "can_sign": True, "can_change_bank": True,
       "can_view": True, "can_request_change": True, "can_claim": True, "can_certificates": True,
       "can_manage_members": True}
NONE = {k: False for k in ALL}
ROLE_AUTHORITY = {
    "owner": ALL,
    "bookkeeper": {**NONE, "can_pay": True, "can_view": True, "can_request_change": True,
                   "can_claim": True, "can_certificates": True},
}


def effective(m: dict) -> dict:
    """Stored switches win; switches added later default from the membership role (R1.2b)."""
    return {**ROLE_AUTHORITY.get(m["role"], NONE), **(m.get("authority") or {})}


async def memberships_for_user(store, tenant_id: str, user_id: str) -> list[dict[str, Any]]:
    safe = wrap(store)
    async with use_tenant(tenant_id):
        rows = [m async for m in safe.memberships.find({"user_id": user_id, "revoked_at": None})]
        owners = {}
        for m in rows:
            o = await safe.memberships.find_one({"client_id": m["client_id"], "role": "owner", "revoked_at": None})
            u = await safe.users.find_one({"user_id": o["user_id"]}) if o else None
            owners[m["client_id"]] = (u or {}).get("display_name")
    return [{"membership_id": m["membership_id"], "client_id": m["client_id"],
             "role": m["role"], "authority": effective(m),
             "owner_name": owners.get(m["client_id"])} for m in rows]


async def require_authority(
    store, session, *, client_id: str, action: str, needed: str,
    policy_id: str | None = None, envelope_id: str | None = None,
) -> None:
    safe = wrap(store)
    async with use_tenant(session.tenant_id):
        m = await safe.memberships.find_one(
            {"user_id": session.user_id, "client_id": client_id, "revoked_at": None})
        if m:
            authority, membership_id = effective(m), m["membership_id"]
        else:
            # No membership row yet: fall back to the legacy link role — only for a client this user is linked to.
            link = await safe.user_client_links.find_one({"user_id": session.user_id, "client_id": client_id})
            authority = ALL if (link and link.get("role") == "client_owner") else NONE
            membership_id = None
        allowed = bool(authority.get(needed))
        await safe.authority_events.insert_one({
            "event_id": str(uuid.uuid4()),
            "client_id": client_id,
            "user_id": session.user_id,
            "membership_id": membership_id,
            "action": action,
            "outcome": "allowed" if allowed else "denied",
            "policy_id": policy_id,
            "envelope_id": envelope_id,
            "ts": datetime.now(timezone.utc),
            "requested_authority": needed,
            "actual_authority": authority,
        })
    if not allowed:
        raise HTTPException(status_code=403, detail={
            "code": "AuthorityDenied",
            "detail": f"your role on this client does not include {needed}",
            "action": action,
        })


async def client_for_session(store, session) -> str | None:
    """The client this session acts for: its bound client, else the user's first link."""
    if session.client_id:
        return session.client_id
    async with use_tenant(session.tenant_id):
        link = await wrap(store).user_client_links.find_one({"user_id": session.user_id})
    return link["client_id"] if link else None


async def require_client_authority(store, session, *, action: str, needed: str) -> str:
    """require_authority for routes that act on the session's client rather than a policy."""
    client_id = await client_for_session(store, session)
    if not client_id:
        raise HTTPException(status_code=404, detail="client_not_linked")
    await require_authority(store, session, client_id=client_id, action=action, needed=needed)
    return client_id
