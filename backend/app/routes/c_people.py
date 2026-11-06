"""R3.12 J6 — client "People on this account": list members, owners invite with a preset, change rights."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.db.tenant_safe import wrap
from app.kernel import auth_flows as af
from app.kernel.authority import ALL, NONE, client_for_session, effective, require_authority
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_session

router = APIRouter(prefix="/api/c/people", tags=["people"])

PLAIN = {"can_view": "see cover and documents", "can_pay": "pay", "can_lodge_claim": "make claims",
         "can_request_change": "ask for changes", "can_approve": "approve renewals and changes",
         "can_sign": "accept and sign", "can_certificates": "get certificates", "can_change_bank": "change bank details",
         "can_manage_members": "manage people"}
CUSTOM = ("can_pay", "can_lodge_claim", "can_request_change", "can_accept")
PRESETS = {
    "bookkeeper": ("bookkeeper", {**NONE, "can_view": True, "can_pay": True}),
    "co_owner": ("owner", ALL),
    "read_only": ("bookkeeper", {**NONE, "can_view": True}),
}
PRESET_LABEL = {"bookkeeper": "Bookkeeper", "co_owner": "Co-owner", "read_only": "Read only", "custom": "Custom"}


class Rights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    can_pay: bool = False
    can_lodge_claim: bool = False
    can_request_change: bool = False
    can_accept: bool = False


class InviteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    preset: Literal["bookkeeper", "co_owner", "read_only", "custom"]
    custom: Rights | None = None


class PresetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset: Literal["bookkeeper", "read_only", "custom"]
    custom: Rights | None = None


def resolve(preset: str, custom: Rights | None) -> tuple[str, dict]:
    if preset != "custom":
        return PRESETS[preset]
    c = custom or Rights()
    return "bookkeeper", {**NONE, "can_view": True, "can_pay": c.can_pay, "can_lodge_claim": c.can_lodge_claim,
                          "can_request_change": c.can_request_change, "can_approve": c.can_accept, "can_sign": c.can_accept}


def plain(auth: dict) -> str:
    if all(auth.get(k) for k in ALL):
        return "Can do everything"
    can = [PLAIN[k] for k in PLAIN if auth.get(k)]
    return ("Can " + ", ".join(can[:-1]) + (" and " if len(can) > 1 else "") + can[-1]) if can else "Can't do anything yet"


def preset_of(m: dict) -> str:
    if m["role"] == "owner":
        return "co_owner"
    a = effective(m)
    for k, (_, auth) in PRESETS.items():
        if k != "co_owner" and all(bool(a.get(s)) == bool(v) for s, v in auth.items()):
            return k
    return "custom"


async def _client(store, s) -> tuple[str, dict]:
    cid = await client_for_session(store, s)
    async with use_tenant(s.tenant_id):
        mine = await wrap(store).memberships.find_one({"user_id": s.user_id, "client_id": cid, "revoked_at": None}) if cid else None
    if not mine:
        raise HTTPException(status_code=403, detail="client_only")
    return cid, mine


@router.get("")
async def people(request: Request, s=Depends(require_session)) -> dict:
    store = request.app.state.mongo_db
    cid, mine = await _client(store, s)
    manager = bool(effective(mine).get("can_manage_members"))
    safe = wrap(store)
    async with use_tenant(s.tenant_id):
        rows = [m async for m in safe.memberships.find({"client_id": cid, "revoked_at": None}, {"_id": 0})]
        users = {u["user_id"]: u async for u in safe.users.find({"user_id": {"$in": [m["user_id"] for m in rows]}}, {"_id": 0})}
    members = []
    for m in sorted(rows, key=lambda m: (m["role"] != "owner", (users.get(m["user_id"]) or {}).get("display_name") or "")):
        u = users.get(m["user_id"]) or {}
        if u.get("test") or u.get("disabled"):
            continue
        members.append({"membership_id": m["membership_id"], "name": u.get("display_name") or "Member", "me": m["user_id"] == s.user_id,
                        "email": u.get("email") if manager or m["user_id"] == s.user_id else None,
                        "role": m["role"], "role_label": "Owner" if m["role"] == "owner" else
                        "Bookkeeper (own mix of rights)" if preset_of(m) == "custom" else PRESET_LABEL[preset_of(m)],
                        "preset": preset_of(m), "can": plain(effective(m)),
                        "rights": {k: bool(effective(m).get(k)) for k in ("can_pay", "can_lodge_claim", "can_request_change")} |
                        {"can_accept": bool(effective(m).get("can_approve"))}})
    invites = []
    if manager:
        toks = [t async for t in safe.system("auth_tokens").find(
            {"kind": "invite", "tenant_id": s.tenant_id, "client_id": cid, "used_at": None, "revoked_at": None}, {"_id": 0})]
        invites = [{"invite_id": t["token_id"], "name": t["name"], "email": t["email"], "expires_at": t["expires_at"].isoformat(),
                    "can": plain({**effective({"role": t["role"], "authority": t.get("authority")})})}
                   for t in toks if af._aware(t["expires_at"]) > af.now()]
    return {"members": members, "invites": invites, "can_manage": manager, "presets": PRESET_LABEL}


@router.post("/invite")
async def invite(body: InviteBody, request: Request, s=Depends(require_session)) -> dict:
    store = request.app.state.mongo_db
    cid, mine = await _client(store, s)
    await require_authority(store, s, client_id=cid, action="invite_member_attempt", needed="can_manage_members")
    role, auth = resolve(body.preset, body.custom)
    held = effective(mine)
    if any(v and not held.get(k) for k, v in auth.items()):
        raise HTTPException(status_code=403, detail="You can't give someone rights you don't hold.")
    token, doc = await af.create_invite(store, tenant_id=s.tenant_id, email=str(body.email), name=body.name.strip(), role=role,
                                        client_id=cid, created_by=s.user_id, authority=None if role == "owner" else auth)
    status = await af.send_auth_email(store, s.tenant_id, kind="invite", to=doc["email"], name=doc["name"],
                                      link=af.invite_url(token), role=PRESET_LABEL[body.preset].lower())
    sent = status.startswith("sent:")
    await af.ledger(store, s.tenant_id, "ClientMemberInvited", af.human(s.user_id, s.active_role),
                    {"client_id": cid, "invite_id": doc["token_id"], "preset": body.preset, "role": role, "authority": auth,
                     "email_status": status.split(":")[0]})
    out = {"ok": True, "invite_id": doc["token_id"], "email_sent": sent, "expires_at": doc["expires_at"].isoformat()}
    if not sent:
        out |= {"invite_link": af.invite_url(token), "email_note": "No email was sent. Copy the link and send it to them yourself."}
    return out


@router.post("/invites/{invite_id}/cancel")
async def cancel(invite_id: str, request: Request, s=Depends(require_session)) -> dict:
    store = request.app.state.mongo_db
    cid, _ = await _client(store, s)
    await require_authority(store, s, client_id=cid, action="invite_cancel_attempt", needed="can_manage_members")
    r = await wrap(store).system("auth_tokens").update_one(
        {"kind": "invite", "token_id": invite_id, "tenant_id": s.tenant_id, "client_id": cid, "used_at": None, "revoked_at": None},
        {"$set": {"revoked_at": af.now()}})
    if not r.modified_count:
        raise HTTPException(status_code=404, detail="not_found")
    await af.ledger(store, s.tenant_id, "ClientMemberInviteCancelled", af.human(s.user_id, s.active_role),
                    {"client_id": cid, "invite_id": invite_id})
    return {"ok": True}


@router.put("/{membership_id}/rights")
async def set_rights(membership_id: str, body: PresetBody, request: Request, s=Depends(require_session)) -> dict:
    store = request.app.state.mongo_db
    cid, mine = await _client(store, s)
    async with use_tenant(s.tenant_id):
        m = await wrap(store).memberships.find_one({"membership_id": membership_id, "client_id": cid, "revoked_at": None})
    if m is None:
        raise HTTPException(status_code=404, detail="Member not found.")
    await require_authority(store, s, client_id=cid, action="manage_members_attempt", needed="can_manage_members")
    if m["role"] == "owner":
        raise HTTPException(status_code=409, detail="Owners always hold every authority.")
    _, auth = resolve(body.preset, body.custom)
    if any(v and not effective(mine).get(k) for k, v in auth.items()):
        raise HTTPException(status_code=403, detail="You can't give someone rights you don't hold.")
    before = effective(m)
    async with use_tenant(s.tenant_id):
        await wrap(store).memberships.update_one({"membership_id": membership_id}, {"$set": {"authority": auth}})
        await wrap(store).authority_events.insert_one({
            "event_id": str(uuid.uuid4()), "client_id": cid, "user_id": s.user_id, "membership_id": membership_id,
            "action": "authority_changed", "outcome": "allowed", "before": before, "after": auth,
            "grantee": m["user_id"], "evidence": {"kind": "in_app", "preset": body.preset}, "ts": datetime.now(timezone.utc)})
    await af.ledger(store, s.tenant_id, "AuthorityChanged", af.human(s.user_id, s.active_role),
                    {"client_id": cid, "grantee": m["user_id"], "preset": body.preset, "before": before, "after": auth})
    return {"ok": True, "can": plain(auth)}
