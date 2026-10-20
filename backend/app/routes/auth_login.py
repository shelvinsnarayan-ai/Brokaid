"""R1.2a — one sign-in for staff and clients: password + required TOTP, invites, reset, sessions."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.auth import passwords, totp as totp_mod
from app.auth.sessions import COOKIE_NAME
from app.db.tenant_safe import wrap
from app.kernel import auth_flows as af
from app.kernel.tenant_ctx import use_tenant

router = APIRouter(prefix="/api/auth", tags=["auth"])
BAD = ("We couldn't sign you in. Check your email and password — or, if you're new, ask your broker for an invite.")  # R3.0: no enumeration


class LoginBody(BaseModel):
    email: str
    password: str


class CodeBody(BaseModel):
    code: str | None = None
    recovery_code: str | None = None


class PasswordBody(BaseModel):
    password: str


class ResetBody(BaseModel):
    token: str
    password: str


class ForgotBody(BaseModel):
    email: str


class ChangeBody(BaseModel):
    current_password: str
    new_password: str


def _store(request: Request):
    return request.app.state.mongo_db


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


async def _user(store, row: dict) -> dict:
    async with use_tenant(row["tenant_id"]):
        u = await wrap(store).users.find_one({"user_id": row["user_id"]})
    return af.merged(u, await af.identity(store, u["email"]))


async def _pending(request: Request) -> tuple[dict, dict]:
    store = _store(request)
    row = await af.read_pending(store, request.cookies.get(COOKIE_NAME))
    if row is None:
        raise HTTPException(status_code=401, detail="Your sign-in timed out. Please start again.")
    return row, await _user(store, row)


async def _start(store, email: str, response: Response, via: str = "password") -> dict:
    """Password (or Google) accepted → pending session in the person's first brokerage."""
    opts = await af.tenant_options(store, email)
    if not opts:
        raise HTTPException(status_code=403, detail="Your account has no access yet. Ask your brokerage.")
    o = opts[0]
    ctx = await af.login_context(store, o["user"], o["tenant_id"])
    sid = await af.start_pending(store, o["user"], o["tenant_id"], ctx, via=via)
    af.set_cookie(response, sid, int(af.PENDING_TTL.total_seconds()))
    return {"next": "totp" if o["user"].get("mfa_secret") else "enrol"}


def _tenants(opts: list[dict], current: str) -> list[dict]:
    return [{"tenant_id": o["tenant_id"], "name": o["name"], "current": o["tenant_id"] == current} for o in opts]


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    store, email, ip = _store(request), body.email.strip().lower(), _ip(request)
    await af.check_locked(store, [f"acct:{email}", f"ip:{ip}"])
    user, tid = await af.credentialed(store, email)
    ok = bool(user and user.get("password_hash") and passwords.verify_password(user["password_hash"], body.password))
    if ok and not await af.tenant_options(store, email):
        ok = False
    if not ok:
        locked = await af.record_failure(store, email, ip)
        if user:
            await af.ledger(store, tid, "LoginFailed", af.human(user["user_id"], "unknown"), {"stage": "password"})
            if locked:
                await af.ledger(store, tid, "AccountLocked", af.human(user["user_id"], "unknown"), {"minutes": 15})
        if locked:
            raise HTTPException(status_code=429, detail="Too many attempts. Try again in 15 minutes.")
        raise HTTPException(status_code=401, detail=BAD)
    return await _start(store, email, response)


@router.post("/login/verify")
async def login_verify(body: CodeBody, request: Request, response: Response) -> dict:
    store = _store(request)
    row, user = await _pending(request)
    email, ip = user["email"], _ip(request)
    await af.check_locked(store, [f"acct:{email}", f"ip:{ip}"])
    if not user.get("mfa_secret"):
        raise HTTPException(status_code=409, detail="Set up your authenticator first.")
    used_recovery = False
    if body.recovery_code:
        ok = used_recovery = await af.use_recovery_code(store, email, body.recovery_code)
    else:
        ok = totp_mod.verify_code(user["mfa_secret"], (body.code or "").replace(" ", ""))
    actor = af.human(user["user_id"], row["active_role"])
    if not ok:
        locked = await af.record_failure(store, email, ip)
        await af.ledger(store, row["tenant_id"], "LoginFailed", actor, {"stage": "recovery" if body.recovery_code else "totp"})
        if locked:
            await af.ledger(store, row["tenant_id"], "AccountLocked", actor, {"minutes": 15})
            raise HTTPException(status_code=429, detail="Too many attempts. Try again in 15 minutes.")
        raise HTTPException(status_code=401, detail="That code didn't work. Check your authenticator app and try again.")
    await af.clear_failures(store, email)
    sid, max_age, redirect = await af.finalise(store, row)
    af.set_cookie(response, sid, max_age)
    if used_recovery:
        left = sum(1 for c in user.get("recovery_codes", []) if c["used_at"] is None) - 1
        await af.ledger(store, row["tenant_id"], "RecoveryCodeUsed", actor, {"remaining": left})
    await af.ledger(store, row["tenant_id"], "LoginSucceeded", actor,
                    {"method": "recovery_code" if used_recovery else "totp", "via": row.get("via", "password")})
    return {"redirect": redirect, "role": row["active_role"],
            "tenants": _tenants(await af.tenant_options(store, email), row["tenant_id"])}


@router.post("/mfa/setup")
async def mfa_setup(request: Request) -> dict:
    store = _store(request)
    row, user = await _pending(request)
    if user.get("mfa_secret"):
        raise HTTPException(status_code=409, detail="Your authenticator is already set up.")
    secret = row.get("pending_secret") or totp_mod.new_secret()
    await wrap(store).system("sessions").update_one({"session_id": row["session_id"]}, {"$set": {"pending_secret": secret}})
    uri = totp_mod.otpauth_uri(secret, account=user["email"])
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": totp_mod.qr_svg(uri)}


@router.post("/mfa/confirm")
async def mfa_confirm(body: CodeBody, request: Request, response: Response) -> dict:
    store = _store(request)
    row, user = await _pending(request)
    secret = row.get("pending_secret")
    if user.get("mfa_secret") or not secret:
        raise HTTPException(status_code=409, detail="Start authenticator setup first.")
    if not totp_mod.verify_code(secret, (body.code or "").replace(" ", "")):
        raise HTTPException(status_code=401, detail="That code didn't work. Check the time on your phone and try again.")
    codes, hashed = af.new_recovery_codes()
    await af.set_identity(store, user["email"], {"mfa_secret": secret, "recovery_codes": hashed, "mfa_enrolled_at": af.now()})
    sid, max_age, redirect = await af.finalise(store, row)
    af.set_cookie(response, sid, max_age)
    actor = af.human(user["user_id"], row["active_role"])
    await af.ledger(store, row["tenant_id"], "MfaEnrolled", actor, {"user_id": user["user_id"]})
    await af.ledger(store, row["tenant_id"], "LoginSucceeded", actor, {"method": "enrolment"})
    return {"redirect": redirect, "recovery_codes": codes,
            "tenants": _tenants(await af.tenant_options(store, user["email"]), row["tenant_id"])}


@router.get("/invite/{token}")
async def invite_info(token: str, request: Request) -> dict:
    store = _store(request)
    inv = await af.live_token(store, "invite", token)
    if inv is None:
        raise HTTPException(status_code=410, detail="This invite link has expired or was already used. Ask for a new one.")
    tenant = await wrap(store).system("tenants").find_one({"tenant_id": inv["tenant_id"]})
    existing, _ = await af.credentialed(store, inv["email"])
    out = {"email": inv["email"], "name": inv["name"], "role": af.ROLE_LABEL[inv["role"]],
           "brokerage": tenant["display_name"], "kind": "client" if inv["role"] in af.CLIENT_ROLES else "staff",
           "existing_account": bool(existing and existing.get("mfa_secret"))}
    if out["kind"] == "client":
        from app.kernel import compliance_docs as cd
        st = await cd.settings(store, inv["tenant_id"])
        out |= {"collection_notice": st["collection_notice_text"], "privacy_policy": st["privacy_policy_text"]}
    return out


@router.post("/invite/{token}/accept")
async def invite_accept(token: str, body: PasswordBody, request: Request, response: Response) -> dict:
    store = _store(request)
    inv = await af.live_token(store, "invite", token)
    if inv is None:
        raise HTTPException(status_code=410, detail="This invite link has expired or was already used. Ask for a new one.")
    existing, _ = await af.credentialed(store, inv["email"])
    existing = existing if existing and existing.get("mfa_secret") else None
    if existing:
        await af.check_locked(store, [f"acct:{inv['email']}", f"ip:{_ip(request)}"])
        if not passwords.verify_password(existing["password_hash"], body.password):
            await af.record_failure(store, inv["email"], _ip(request))
            raise HTTPException(status_code=401, detail="That isn't your current brokAId password.")
    else:
        problem = af.password_problem(body.password, inv["email"])
        if problem:
            raise HTTPException(status_code=422, detail=problem)
    if not await af.use_token(store, inv):
        raise HTTPException(status_code=410, detail="This invite link was already used.")
    user = await af.apply_invite(store, inv, body.password, existing)
    await af.revoke_user_sessions(store, inv["tenant_id"], user["user_id"])
    ctx = await af.login_context(store, user, inv["tenant_id"])
    await af.ledger(store, inv["tenant_id"], "InviteAccepted", af.human(user["user_id"], ctx["active_role"]),
                    {"invite_id": inv["token_id"], "role": inv["role"], "client_id": inv.get("client_id"),
                     "existing_account": bool(existing)})
    if inv["role"] in af.CLIENT_ROLES and inv.get("client_id"):  # R1.7: FSG before the first service + collection notice record
        from app.kernel import compliance_docs as cd
        await cd.on_client_onboarded(store, inv["tenant_id"], inv["client_id"], user["user_id"], "invite_acceptance")
    sid = await af.start_pending(store, user, inv["tenant_id"], ctx)
    af.set_cookie(response, sid, int(af.PENDING_TTL.total_seconds()))
    return {"next": "totp" if existing else "enrol"}


@router.post("/password/forgot")
async def forgot(body: ForgotBody, request: Request) -> dict:
    store, email = _store(request), body.email.strip().lower()
    user, tid = await af.credentialed(store, email)
    if user:
        recent = await wrap(store).system("auth_tokens").count_documents(
            {"kind": "reset", "email": email, "created_at": {"$gt": af.now() - af.RESET_TTL}})
        if recent < 3:
            token, _ = await af.issue_token(store, kind="reset", tenant_id=tid, email=email, ttl=af.RESET_TTL,
                                            user_id=user["user_id"])
            status = await af.send_auth_email(store, tid, kind="reset", to=email, name=user["display_name"],
                                              link=af.reset_url(token))
            await af.ledger(store, tid, "PasswordResetRequested", af.human(user["user_id"], "self"),
                            {"email_status": status.split(":")[0]})
    return {"ok": True, "message": "If that email has an account, we've sent a reset link. It works for 1 hour."}


@router.get("/password/reset/{token}")
async def reset_info(token: str, request: Request) -> dict:
    if await af.live_token(_store(request), "reset", token) is None:
        raise HTTPException(status_code=410, detail="This reset link has expired or was already used.")
    return {"valid": True}


@router.post("/password/reset")
async def reset(body: ResetBody, request: Request) -> dict:
    store = _store(request)
    row = await af.live_token(store, "reset", body.token)
    if row is None:
        raise HTTPException(status_code=410, detail="This reset link has expired or was already used.")
    problem = af.password_problem(body.password, row["email"])
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    if not await af.use_token(store, row):
        raise HTTPException(status_code=410, detail="This reset link was already used.")
    await af.set_identity(store, row["email"], {"password_hash": passwords.hash_password(body.password),
                                                "password_set_at": af.now()})
    n = await af.revoke_everywhere(store, row["email"])
    await af.clear_failures(store, row["email"])
    await af.ledger(store, row["tenant_id"], "PasswordChanged", af.human(row["user_id"], "self"),
                    {"via": "reset_link", "sessions_revoked": n})
    return {"ok": True}


async def _live(request: Request):
    from app.middleware.session import require_session
    return await require_session(request)


@router.post("/password/change")
async def change_password(body: ChangeBody, request: Request) -> dict:
    s = await _live(request)
    store = _store(request)
    async with use_tenant(s.tenant_id):
        user = await wrap(store).users.find_one({"user_id": s.user_id})
    user = af.merged(user, await af.identity(store, user["email"]))
    if not user.get("password_hash") or not passwords.verify_password(user["password_hash"], body.current_password):
        raise HTTPException(status_code=401, detail="Your current password is wrong.")
    problem = af.password_problem(body.new_password, user["email"])
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    await af.set_identity(store, user["email"], {"password_hash": passwords.hash_password(body.new_password),
                                                 "password_set_at": af.now()})
    n = await af.revoke_everywhere(store, user["email"], keep=s.session_id)
    await af.ledger(store, s.tenant_id, "PasswordChanged", af.human(s.user_id, s.active_role),
                    {"via": "profile", "sessions_revoked": n})
    return {"ok": True, "other_sessions_signed_out": n}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    from app.kernel import identity
    sid = request.cookies.get(COOKIE_NAME)
    if sid:
        await identity.revoke_session(_store(request), sid)
    af.clear_cookie(response)
    return {"ok": True}


@router.post("/sessions/revoke-others")
async def revoke_others(request: Request) -> dict:
    s = await _live(request)
    n = await af.revoke_user_sessions(_store(request), s.tenant_id, s.user_id, keep=s.session_id)
    await af.ledger(_store(request), s.tenant_id, "SessionsRevoked", af.human(s.user_id, s.active_role),
                    {"scope": "other_devices", "count": n})
    return {"ok": True, "signed_out": n}


class GoogleBody(BaseModel):
    session_id: str


class TenantBody(BaseModel):
    tenant_id: str


@router.post("/google")
async def google_sign_in(body: GoogleBody, request: Request, response: Response) -> dict:
    """Emergent-managed Google Auth: matches an existing invited person by verified email; TOTP still required."""
    store = _store(request)
    profile = await af.google_profile(body.session_id)
    email = profile["email"]
    await af.check_locked(store, [f"acct:{email}", f"ip:{_ip(request)}"])
    user, tid = await af.credentialed(store, email)
    if user is None or not await af.tenant_options(store, email):
        raise HTTPException(status_code=403, detail=f"There's no brokAId account for {email}. Ask your brokerage for an invite.")
    return await _start(store, email, response, via="google")


async def _live_user(request: Request):
    s = await _live(request)
    async with use_tenant(s.tenant_id):
        u = await wrap(_store(request)).users.find_one({"user_id": s.user_id})
    return s, af.merged(u, await af.identity(_store(request), u["email"]))


@router.post("/google/link")
async def google_link(body: GoogleBody, request: Request) -> dict:
    s, user = await _live_user(request)
    profile = await af.google_profile(body.session_id)
    if profile["email"] != user["email"]:
        raise HTTPException(status_code=409, detail=f"That Google account ({profile['email']}) doesn't match your sign-in email.")
    await af.set_identity(_store(request), user["email"], {"google_email": profile["email"], "google_linked_at": af.now()})
    await af.ledger(_store(request), s.tenant_id, "GoogleLinked", af.human(s.user_id, s.active_role), {"user_id": s.user_id})
    return {"ok": True, "google_email": profile["email"]}


@router.get("/account")
async def account(request: Request) -> dict:
    s, user = await _live_user(request)
    opts = await af.tenant_options(_store(request), user["email"])
    return {"email": user["email"], "name": user["display_name"], "google_email": user.get("google_email"),
            "tenants": _tenants(opts, s.tenant_id)}


@router.post("/switch-tenant")
async def switch_tenant(body: TenantBody, request: Request, response: Response) -> dict:
    """Each session is bound to one tenant; switching revokes it and issues a new one for the target tenant."""
    s, user = await _live_user(request)
    store = _store(request)
    opt = next((o for o in await af.tenant_options(store, user["email"]) if o["tenant_id"] == body.tenant_id), None)
    if opt is None:
        raise HTTPException(status_code=404, detail="You don't have access to that brokerage.")
    if body.tenant_id == s.tenant_id:
        return {"redirect": "/o" if s.active_role not in ("client_owner", "client_contact", "client_finance") else "/c"}
    ctx = await af.login_context(store, opt["user"], opt["tenant_id"])
    from app.kernel import identity
    await identity.revoke_session(store, s.session_id)
    sid, max_age, redirect = await af.issue_session(store, opt["tenant_id"], opt["user"]["user_id"], ctx)
    af.set_cookie(response, sid, max_age)
    await af.ledger(store, opt["tenant_id"], "TenantSwitched", af.human(opt["user"]["user_id"], ctx["active_role"]),
                    {"from_tenant": s.tenant_id})
    return {"redirect": redirect}
