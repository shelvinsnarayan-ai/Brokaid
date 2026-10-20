"""R1.2b — A02 client members: authority switches, member removal guard, client switcher, "your broker"."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ConfigDict, BaseModel

from app.db.tenant_safe import wrap
from app.kernel import auth_flows as af
from app.kernel.authority import client_for_session, effective, require_authority
from app.kernel.notify import assigned_ae
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_session

router = APIRouter(prefix="/api/c", tags=["members"])


class SwitchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")  # R2.6: unknown fields are a 422
    switch: str
    on: bool


class ClientBody(BaseModel):
    model_config = ConfigDict(extra="forbid")  # R2.6: unknown fields are a 422
    client_id: str


async def _member(store, s, membership_id: str) -> dict:
    async with use_tenant(s.tenant_id):
        m = await wrap(store).memberships.find_one({"membership_id": membership_id, "revoked_at": None})
    if m is None or m["client_id"] != await client_for_session(store, s):
        raise HTTPException(status_code=404, detail="Member not found.")
    await require_authority(store, s, client_id=m["client_id"], action="manage_members_attempt", needed="can_manage_members")
    return m


@router.put("/members/{membership_id}/authority")
async def set_authority(membership_id: str, body: SwitchBody, request: Request, s=Depends(require_session)) -> dict:
    store = request.app.state.mongo_db
    m = await _member(store, s, membership_id)
    if m["role"] == "owner":
        raise HTTPException(status_code=409, detail="Owners always hold every authority.")
    before = effective(m)
    if body.switch not in before:
        raise HTTPException(status_code=422, detail="Unknown authority switch.")
    async with use_tenant(s.tenant_id):
        mine = await wrap(store).memberships.find_one({"user_id": s.user_id, "client_id": m["client_id"], "revoked_at": None})
    if body.on and not effective(mine).get(body.switch):
        raise HTTPException(status_code=403, detail="You can't grant an authority you don't hold.")
    after = {**before, body.switch: body.on}
    async with use_tenant(s.tenant_id):
        await wrap(store).memberships.update_one({"membership_id": membership_id}, {"$set": {"authority": after}})
        await wrap(store).authority_events.insert_one({
            "event_id": str(uuid.uuid4()), "client_id": m["client_id"], "user_id": s.user_id, "membership_id": membership_id,
            "action": "authority_changed", "outcome": "allowed", "before": before, "after": after,
            "grantee": m["user_id"], "evidence": {"kind": "in_app"}, "ts": datetime.now(timezone.utc)})
    await af.ledger(store, s.tenant_id, "AuthorityChanged", af.human(s.user_id, s.active_role),
                    {"client_id": m["client_id"], "grantee": m["user_id"], "switch": body.switch, "before": before[body.switch],
                     "after": body.on})
    return {"ok": True, "authority": after}


@router.post("/members/{membership_id}/remove")
async def remove_member(membership_id: str, request: Request, s=Depends(require_session)) -> dict:
    store = request.app.state.mongo_db
    m = await _member(store, s, membership_id)
    if m["role"] == "owner":
        raise HTTPException(status_code=409, detail="Removing an Owner needs a second Owner or your broker to confirm.")
    async with use_tenant(s.tenant_id):
        await wrap(store).memberships.update_one({"membership_id": membership_id},
                                                 {"$set": {"revoked_at": datetime.now(timezone.utc), "revoked_by": s.user_id}})
        await wrap(store).user_client_links.delete_many({"user_id": m["user_id"], "client_id": m["client_id"]})
    await af.revoke_user_sessions(store, s.tenant_id, m["user_id"])
    await af.ledger(store, s.tenant_id, "MemberRemoved", af.human(s.user_id, s.active_role),
                    {"client_id": m["client_id"], "membership_id": membership_id, "user_id": m["user_id"]})
    return {"ok": True}


@router.post("/switch-client")
async def switch_client(body: ClientBody, request: Request, s=Depends(require_session)) -> dict:
    from app.kernel import identity
    store = request.app.state.mongo_db
    async with use_tenant(s.tenant_id):
        link = await wrap(store).user_client_links.find_one({"user_id": s.user_id, "client_id": body.client_id})
    if link is None:
        raise HTTPException(status_code=404, detail="Client not found.")
    await identity.update_active_context(store, s.session_id, tenant_id=s.tenant_id, client_id=body.client_id,
                                         active_role=link["role"])
    return {"ok": True, "client_id": body.client_id}


@router.get("/broker")
async def my_broker(request: Request, s=Depends(require_session)) -> dict:
    store = request.app.state.mongo_db
    cid = await client_for_session(store, s)
    safe = wrap(store)
    async with use_tenant(s.tenant_id):
        uid = await assigned_ae(safe, cid)
        u = await safe.users.find_one({"user_id": uid}) if uid else None
    return {"name": (u or {}).get("display_name")}
