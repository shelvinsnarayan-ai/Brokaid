"""R1.2a — minimal Operator invite screen API + MFA reset / session revoke."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.db.tenant_safe import wrap
from app.kernel import auth_flows as af
from app.kernel.tenant_ctx import use_tenant
from app.kernel.test_data import test_client_ids
from app.middleware.session import require_operator_session

router = APIRouter(prefix="/api/o", tags=["invites"])


class InviteBody(BaseModel):
    email: str
    name: str = ""
    role: str
    client_id: str | None = None


def _inviter(session) -> None:
    if session.active_role not in ("operator", "principal"):
        raise HTTPException(status_code=403, detail="Only the Operator or Principal can invite people.")


def _may_invite(session, role: str) -> None:
    if session.active_role == "principal" and role not in af.PRINCIPAL_MAY_INVITE and role not in af.CLIENT_ROLES:
        raise HTTPException(status_code=403, detail="Principals can invite staff and clients, not Operators or Principals.")


def _view(t: dict) -> dict:
    state = "accepted" if t.get("used_at") else "revoked" if t.get("revoked_at") else \
        "expired" if af._aware(t["expires_at"]) < af.now() else "open"
    return {"invite_id": t["token_id"], "email": t["email"], "name": t["name"], "role": t["role"],
            "role_label": af.ROLE_LABEL[t["role"]], "client_id": t.get("client_id"), "state": state,
            "expires_at": t["expires_at"].isoformat(), "created_at": t["created_at"].isoformat()}


async def _send(store, s, doc: dict, token: str) -> str:
    return await af.send_auth_email(store, s.tenant_id, kind="invite", to=doc["email"], name=doc["name"],
                                    link=af.invite_url(token), role=af.ROLE_LABEL[doc["role"]])


@router.get("/invites")
async def list_invites(request: Request, s=Depends(require_operator_session)) -> dict:
    _inviter(s)
    store, safe = request.app.state.mongo_db, wrap(request.app.state.mongo_db)
    async with use_tenant(s.tenant_id):
        hidden = set(await test_client_ids(safe))
        clients = [{"client_id": c["client_id"], "name": c["display_name"]}
                   async for c in safe.clients.find({}).sort("display_name", 1) if c["client_id"] not in hidden]
    rows = [_view(t) async for t in safe.system("auth_tokens").find(
        {"kind": "invite", "tenant_id": s.tenant_id}).sort("created_at", -1).limit(50)]
    roles = [r for r in af.STAFF_ROLES if s.active_role == "operator" or r in af.PRINCIPAL_MAY_INVITE]
    return {"invites": rows, "clients": clients,
            "roles": [{"value": r, "label": af.ROLE_LABEL[r]} for r in roles + list(af.CLIENT_ROLES)]}


@router.post("/invites")
async def create(body: InviteBody, request: Request, s=Depends(require_operator_session)) -> dict:
    _inviter(s)
    _may_invite(s, body.role)
    store = request.app.state.mongo_db
    if "@" not in body.email or "." not in body.email.split("@")[-1]:
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    if body.client_id:
        async with use_tenant(s.tenant_id):
            if not await wrap(store).clients.find_one({"client_id": body.client_id}):
                raise HTTPException(status_code=404, detail="Client not found.")
    token, doc = await af.create_invite(store, tenant_id=s.tenant_id, email=body.email, name=body.name,
                                        role=body.role, client_id=body.client_id, created_by=s.user_id)
    status = await _send(store, s, doc, token)
    await af.ledger(store, s.tenant_id, "InviteCreated", af.human(s.user_id, s.active_role),
                    {"invite_id": doc["token_id"], "role": doc["role"], "client_id": doc.get("client_id"),
                     "email_status": status.split(":")[0]})
    return {"invite": _view(doc), "invite_url": af.invite_url(token), "email_status": status.split(":")[0]}


async def _own_invite(store, s, invite_id: str) -> dict:
    row = await wrap(store).system("auth_tokens").find_one({"kind": "invite", "token_id": invite_id, "tenant_id": s.tenant_id})
    if row is None:
        raise HTTPException(status_code=404, detail="Invite not found.")
    _may_invite(s, row["role"])
    return row


@router.post("/invites/{invite_id}/reissue")
async def reissue(invite_id: str, request: Request, s=Depends(require_operator_session)) -> dict:
    _inviter(s)
    store = request.app.state.mongo_db
    old = await _own_invite(store, s, invite_id)
    if old.get("used_at"):
        raise HTTPException(status_code=409, detail="This invite was already accepted.")
    token, doc = await af.create_invite(store, tenant_id=s.tenant_id, email=old["email"], name=old["name"],
                                        role=old["role"], client_id=old.get("client_id"), created_by=s.user_id)
    status = await _send(store, s, doc, token)
    await af.ledger(store, s.tenant_id, "InviteReissued", af.human(s.user_id, s.active_role),
                    {"invite_id": doc["token_id"], "replaces": invite_id, "email_status": status.split(":")[0]})
    return {"invite": _view(doc), "invite_url": af.invite_url(token), "email_status": status.split(":")[0]}


@router.post("/invites/{invite_id}/revoke")
async def revoke(invite_id: str, request: Request, s=Depends(require_operator_session)) -> dict:
    _inviter(s)
    store = request.app.state.mongo_db
    await _own_invite(store, s, invite_id)
    await wrap(store).system("auth_tokens").update_one({"token_id": invite_id, "used_at": None},
                                                         {"$set": {"revoked_at": af.now()}})
    await af.ledger(store, s.tenant_id, "InviteRevoked", af.human(s.user_id, s.active_role), {"invite_id": invite_id})
    return {"ok": True}


async def _target(store, s, user_id: str) -> dict:
    if s.active_role != "operator":
        raise HTTPException(status_code=403, detail="Only the Operator can do this.")
    async with use_tenant(s.tenant_id):
        u = await wrap(store).users.find_one({"user_id": user_id})
    if u is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return u


@router.post("/users/{user_id}/reset-mfa")
async def reset_mfa(user_id: str, request: Request, s=Depends(require_operator_session)) -> dict:
    store = request.app.state.mongo_db
    u = await _target(store, s, user_id)
    async with use_tenant(s.tenant_id):
        await wrap(store).users.update_one({"user_id": user_id}, {"$set": {"mfa_secret": None, "recovery_codes": []}})
    n = await af.revoke_user_sessions(store, s.tenant_id, user_id)
    status = await af.send_auth_email(store, s.tenant_id, kind="mfa_reset", to=u["email"], name=u["display_name"],
                                      link=f"{af.base_url()}/login")
    await af.ledger(store, s.tenant_id, "MfaReset", af.human(s.user_id, s.active_role),
                    {"user_id": user_id, "sessions_revoked": n, "email_status": status.split(":")[0]})
    return {"ok": True, "sessions_revoked": n, "email_status": status.split(":")[0]}


@router.post("/users/{user_id}/revoke-sessions")
async def revoke_sessions(user_id: str, request: Request, s=Depends(require_operator_session)) -> dict:
    store = request.app.state.mongo_db
    await _target(store, s, user_id)
    n = await af.revoke_user_sessions(store, s.tenant_id, user_id, keep=s.session_id)
    await af.ledger(store, s.tenant_id, "SessionsRevoked", af.human(s.user_id, s.active_role),
                    {"user_id": user_id, "count": n})
    return {"ok": True, "signed_out": n}
