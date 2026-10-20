"""R1.2b — /o/users admin + client AE assignment. Every action is a ledger event."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.db.tenant_safe import wrap
from app.kernel import auth_flows as af
from app.kernel.authority import ROLE_AUTHORITY
from app.kernel.notify import active_ae, assigned_ae
from app.kernel.tenant_ctx import use_tenant
from app.kernel.test_data import test_client_ids
from app.middleware.session import require_operator_session

router = APIRouter(prefix="/api/o", tags=["users"])
PRIVILEGED = ("operator", "principal")


class RoleBody(BaseModel):
    role: str


class AeBody(BaseModel):
    user_id: str | None = None


def manager(s) -> None:
    if s.active_role not in PRIVILEGED:
        raise HTTPException(status_code=403, detail="Only the Operator or Principal can manage people.")


async def _roles(safe, uid: str) -> list[str]:
    return [r["role"] async for r in safe.user_brokerage_roles.find({"user_id": uid, "active": True})]


async def target(store, s, user_id: str) -> tuple[dict, list[str]]:
    """The user in this tenant. Principals can't act on Operators/Principals (or themselves)."""
    manager(s)
    safe = wrap(store)
    async with use_tenant(s.tenant_id):
        u = await safe.users.find_one({"user_id": user_id})
        roles = await _roles(safe, user_id) if u else []
    if u is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if s.active_role == "principal" and (set(roles) & set(PRIVILEGED)):
        raise HTTPException(status_code=403, detail="Principals can't change Operators or Principals.")
    if user_id == s.user_id:
        raise HTTPException(status_code=409, detail="You can't do this to your own account.")
    return u, roles


def _status(u: dict) -> str:
    return "disabled" if u.get("disabled") else "active" if u.get("password_set_at") else "invited"


@router.get("/users")
async def list_users(request: Request, s=Depends(require_operator_session)) -> dict:
    manager(s)
    store = request.app.state.mongo_db
    safe = wrap(store)
    async with use_tenant(s.tenant_id):
        hidden = set(await test_client_ids(safe))
        clients = {c["client_id"]: c async for c in safe.clients.find({}) if c["client_id"] not in hidden}
        users = []
        async for u in safe.users.find({"test": {"$ne": True}}).sort("display_name", 1):
            u = af.merged(u, await af.identity(store, u["email"]))
            roles = await _roles(safe, u["user_id"])
            links = [m async for m in safe.memberships.find({"user_id": u["user_id"], "revoked_at": None})
                     if m["client_id"] in clients]
            if not roles and not links:
                continue
            users.append({"user_id": u["user_id"], "name": u["display_name"], "email": u["email"],
                          "kind": "client" if links and not roles else "staff",
                          "role": roles[0] if roles else links[0]["role"],
                          "client": clients[links[0]["client_id"]]["display_name"] if links else None,
                          "status": _status(u), "mfa": bool(u.get("mfa_secret")), "google": bool(u.get("google_email")),
                          "editable": s.active_role == "operator" or not (set(roles) & set(PRIVILEGED))})
        aes = {u["user_id"]: u["name"] for u in users if u["role"] == "ae" and u["status"] != "disabled"}
        rows = []
        for cid, c in clients.items():
            a = await assigned_ae(safe, cid)
            rows.append({"client_id": cid, "name": c["display_name"], "assigned_ae": a, "assigned_name": aes.get(a)})
    invites = {t["email"]: t["token_id"] async for t in safe.system("auth_tokens").find(
        {"kind": "invite", "tenant_id": s.tenant_id, "used_at": None, "revoked_at": None, "expires_at": {"$gt": af.now()}})}
    for u in users:
        u["open_invite_id"] = invites.get(u["email"])
    roles = [r for r in af.STAFF_ROLES if s.active_role == "operator" or r in af.PRINCIPAL_MAY_INVITE]
    return {"users": users, "clients": sorted(rows, key=lambda r: r["name"]),
            "aes": [{"user_id": k, "name": v} for k, v in aes.items()], "staff_roles": roles}


async def _set_disabled(request, s, user_id: str, disabled: bool) -> dict:
    store = request.app.state.mongo_db
    await target(store, s, user_id)
    async with use_tenant(s.tenant_id):
        await wrap(store).users.update_one({"user_id": user_id}, {"$set": {"disabled": disabled}})
    n = await af.revoke_user_sessions(store, s.tenant_id, user_id) if disabled else 0
    await af.ledger(store, s.tenant_id, "UserDisabled" if disabled else "UserEnabled",
                    af.human(s.user_id, s.active_role), {"user_id": user_id, "sessions_revoked": n})
    return {"ok": True, "sessions_revoked": n}


@router.post("/users/{user_id}/disable")
async def disable(user_id: str, request: Request, s=Depends(require_operator_session)) -> dict:
    return await _set_disabled(request, s, user_id, True)


@router.post("/users/{user_id}/enable")
async def enable(user_id: str, request: Request, s=Depends(require_operator_session)) -> dict:
    return await _set_disabled(request, s, user_id, False)


@router.post("/users/{user_id}/role")
async def change_role(user_id: str, body: RoleBody, request: Request, s=Depends(require_operator_session)) -> dict:
    store = request.app.state.mongo_db
    u, roles = await target(store, s, user_id)
    safe = wrap(store)
    async with use_tenant(s.tenant_id):
        if u["kind"] == "client":
            if body.role not in af.CLIENT_ROLES:
                raise HTTPException(status_code=422, detail="Clients can be an Owner or a Bookkeeper.")
            m = await safe.memberships.find_one({"user_id": user_id, "revoked_at": None})
            before = m["role"]
            await safe.memberships.update_one({"membership_id": m["membership_id"]}, {"$set": {
                "role": body.role, "authority": ROLE_AUTHORITY[body.role]}})
            await safe.user_client_links.update_many({"user_id": user_id, "client_id": m["client_id"]},
                                                     {"$set": {"role": af.CLIENT_ROLES[body.role]}})
        else:
            if body.role not in af.STAFF_ROLES:
                raise HTTPException(status_code=422, detail="Pick a staff role.")
            if s.active_role == "principal" and body.role in PRIVILEGED:
                raise HTTPException(status_code=403, detail="Only the Operator can make someone an Operator or Principal.")
            before = roles[0] if roles else None
            b = await safe.brokerages.find_one({})
            await safe.user_brokerage_roles.update_many({"user_id": user_id, "role": {"$in": list(af.STAFF_ROLES)}},
                                                        {"$set": {"active": False}})
            await safe.user_brokerage_roles.update_one(
                {"user_id": user_id, "role": body.role},
                {"$set": {"active": True, "brokerage_id": b["brokerage_id"]},
                 "$setOnInsert": {"user_id": user_id, "role": body.role}}, upsert=True)
    n = await af.revoke_user_sessions(store, s.tenant_id, user_id)
    await af.ledger(store, s.tenant_id, "UserRoleChanged", af.human(s.user_id, s.active_role),
                    {"user_id": user_id, "before": before, "after": body.role, "sessions_revoked": n})
    return {"ok": True, "before": before, "after": body.role}


@router.get("/clients/{client_id}/assignment")
async def get_assignment(client_id: str, request: Request, s=Depends(require_operator_session)) -> dict:
    safe = wrap(request.app.state.mongo_db)
    async with use_tenant(s.tenant_id):
        c = await safe.clients.find_one({"client_id": client_id})
        if not c:
            raise HTTPException(status_code=404, detail="Client not found.")
        uid = await assigned_ae(safe, client_id)
        stale = c.get("assigned_ae_user_id") if c.get("assigned_ae_user_id") and not uid else None
        aes = [{"user_id": r["user_id"], "name": u.get("display_name")} async for r in safe.user_brokerage_roles.find({"role": "ae", "active": True})
               if not (u := await safe.users.find_one({"user_id": r["user_id"]}) or {}).get("disabled")]
        stale_name = (await safe.users.find_one({"user_id": stale}) or {}).get("display_name") if stale else None
    name = next((a["name"] for a in aes if a["user_id"] == uid), None)
    warning = (f"Assigned AE disabled — reassign. {stale_name or 'The assigned AE'} can't receive alerts; client alerts go to the "
               "Principal and every active AE until you pick someone." if stale else
               None if uid else "No account executive is assigned. Client alerts go to every AE until you pick one.")
    return {"assigned": {"user_id": uid, "name": name} if uid else None, "aes": aes, "assigned_disabled": bool(stale),
            "can_edit": s.active_role in PRIVILEGED, "warning": warning}


@router.put("/clients/{client_id}/assigned-ae")
async def set_assignment(client_id: str, body: AeBody, request: Request, s=Depends(require_operator_session)) -> dict:
    manager(s)
    store = request.app.state.mongo_db
    safe = wrap(store)
    async with use_tenant(s.tenant_id):
        c = await safe.clients.find_one({"client_id": client_id})
        if not c:
            raise HTTPException(status_code=404, detail="Client not found.")
        if body.user_id and not await active_ae(safe, body.user_id):
            raise HTTPException(status_code=422, detail="Pick an active account executive.")
        await safe.clients.update_one({"client_id": client_id}, {"$set": {"assigned_ae_user_id": body.user_id}})
    await af.ledger(store, s.tenant_id, "ClientAeAssigned", af.human(s.user_id, s.active_role),
                    {"client_id": client_id, "before": c.get("assigned_ae_user_id"), "after": body.user_id})
    return {"ok": True}
