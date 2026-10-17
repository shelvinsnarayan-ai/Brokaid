"""R1.2a — real sign-in: invites, reset tokens, lockout, recovery codes, auth email, bootstrap."""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from fastapi import HTTPException

from app.auth import passwords
from app.auth.sessions import COOKIE_NAME
from app.db.tenant_safe import wrap
from app.kernel import email_channel as ec
from app.kernel.authority import ROLE_AUTHORITY
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch

log = logging.getLogger("brokaid.auth")

INVITE_TTL = timedelta(hours=72)
RESET_TTL = timedelta(hours=1)
PENDING_TTL = timedelta(minutes=15)
STAFF_TTL = timedelta(hours=8)
CLIENT_ABS = timedelta(days=7)
CLIENT_IDLE = timedelta(hours=12)
MAX_FAILS, IP_MAX_FAILS, LOCK_FOR = 5, 20, timedelta(minutes=15)
STAFF_ROLES = ("operator", "principal", "deputy", "ae", "accounts", "compliance")
PRINCIPAL_MAY_INVITE = ("deputy", "ae", "accounts", "compliance")
CLIENT_ROLES = {"owner": "client_owner", "bookkeeper": "client_contact"}
ROLE_LABEL = {"operator": "Operator", "principal": "Principal", "deputy": "Deputy", "ae": "Account executive",
              "accounts": "Accounts", "compliance": "Compliance", "owner": "Client owner", "bookkeeper": "Bookkeeper"}
_COMMON = {l.strip().lower() for l in (Path(__file__).parent.parent / "auth" / "common_passwords.txt").read_text().splitlines() if l.strip()}
_WEAK_WORDS = ("password", "passw0rd", "qwerty", "letmein", "welcome", "iloveyou", "admin", "abc123", "monkey",
               "dragon", "sunshine", "princess", "football", "baseball", "superman", "starwars", "trustno1",
               "brokaid", "insurance", "harbourline", "changeme", "123456")


def now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def base_url() -> str:
    return os.environ["APP_BASE_URL"].rstrip("/")


def is_production() -> bool:
    return (os.environ.get("ENVIRONMENT") or "").strip().lower() == "production"


def cookie_flags() -> dict:
    return {"httponly": True, "samesite": "lax", "secure": base_url().startswith("https://") or is_production(), "path": "/"}


def set_cookie(response, session_id: str, max_age: int) -> None:
    response.set_cookie(key=COOKIE_NAME, value=session_id, max_age=max_age, **cookie_flags())


def clear_cookie(response) -> None:
    response.delete_cookie(key=COOKIE_NAME, **cookie_flags())


def password_problem(pw: str, email: str = "") -> str | None:
    if len(pw or "") < 12:
        return "Use at least 12 characters."
    low = pw.lower()
    stripped = low.rstrip("0123456789!@#$%^&*.?_- ")
    local = (email or "").split("@")[0].lower()
    if low in _COMMON or stripped in _COMMON or len(set(low)) < 4 \
            or any(stripped == w or stripped == w * 2 for w in _WEAK_WORDS) \
            or (len(local) >= 4 and local in low):
        return "That password is too common or easy to guess. Try a longer phrase."
    return None


async def ledger(store, tenant_id: str, type: str, actor: dict, payload: dict) -> None:
    try:
        async with use_tenant(tenant_id):
            await append_via_dispatch(store, tenant_id, type, actor=actor, payload=payload,
                                      idempotency_key=f"{type}:{uuid.uuid4()}")
    except Exception:
        log.exception("auth ledger write failed: %s", type)


def human(user_id: str, role: str) -> dict:
    return {"kind": "human", "id": user_id, "name": role}


# ---- lockout (account + IP) -----------------------------------------------
async def check_locked(store, keys: list[str]) -> None:
    safe = wrap(store)
    for k in keys:
        row = await safe.system("auth_lockouts").find_one({"key": k})
        if row and row.get("locked_until") and _aware(row["locked_until"]) > now():
            raise HTTPException(status_code=429, detail="Too many attempts. Try again in 15 minutes.")


async def record_failure(store, email: str, ip: str) -> bool:
    """Returns True if this failure locked the account."""
    safe, t, locked = wrap(store), now(), False
    for k, limit in ((f"acct:{email}", MAX_FAILS), (f"ip:{ip}", IP_MAX_FAILS)):
        row = await safe.system("auth_lockouts").find_one({"key": k}) or {}
        start = row.get("window_start")
        fails = row.get("fails", 0) if start and _aware(start) > t - LOCK_FOR else 0
        fails += 1
        upd = {"key": k, "fails": fails, "window_start": start if fails > 1 else t}
        if fails >= limit:
            upd.update(locked_until=t + LOCK_FOR, fails=0)
            locked = locked or k.startswith("acct:")
        await safe.system("auth_lockouts").update_one({"key": k}, {"$set": upd}, upsert=True)
    return locked


async def clear_failures(store, email: str) -> None:
    await wrap(store).system("auth_lockouts").delete_one({"key": f"acct:{email}"})


# ---- users / roles ----------------------------------------------------------
async def find_user(store, email: str) -> tuple[dict | None, str | None]:
    from app.kernel import identity
    user, tid = await identity.find_user_by_email_across_tenants(store, email.strip().lower())
    if user is None:
        return None, None
    async with use_tenant(tid):
        return await wrap(store).users.find_one({"user_id": user.user_id}), tid


async def login_context(store, user: dict, tenant_id: str) -> dict | None:
    """Pick the session role: staff → first active login role; client → first live link."""
    safe = wrap(store)
    async with use_tenant(tenant_id):
        if user["kind"] == "operator":
            rows = [r async for r in safe.user_brokerage_roles.find({"user_id": user["user_id"], "active": True})]
            for role in STAFF_ROLES:
                for r in rows:
                    if r["role"] == role:
                        return {"active_role": role, "brokerage_id": r["brokerage_id"], "client_id": None, "staff": True}
            return None
        link = await safe.user_client_links.find_one({"user_id": user["user_id"], "archived_at": None}) \
            or await safe.user_client_links.find_one({"user_id": user["user_id"]})
        if link is None:
            return None
        return {"active_role": link["role"], "brokerage_id": None, "client_id": link["client_id"], "staff": False}


async def start_pending(store, user: dict, tenant_id: str, ctx: dict) -> str:
    """Password accepted; session is unusable until TOTP succeeds."""
    sid = str(uuid.uuid4())
    async with use_tenant(tenant_id):
        await wrap(store).sessions.insert_one({
            "session_id": sid, "user_id": user["user_id"], "tenant_id": tenant_id,
            "brokerage_id": ctx["brokerage_id"], "client_id": ctx["client_id"],
            "active_role": ctx["active_role"], "mfa_verified": False, "pending_mfa": True,
            "staff": ctx["staff"], "expires_at": now() + PENDING_TTL, "revoked_at": None,
            "client_prefs": {}, "created_at": now(),
        })
    return sid


async def read_pending(store, sid: str | None) -> dict | None:
    if not sid:
        return None
    row = await wrap(store).system("sessions").find_one({"session_id": sid, "pending_mfa": True, "revoked_at": None})
    if row is None or _aware(row["expires_at"]) < now():
        return None
    return row


async def finalise(store, row: dict) -> tuple[int, str]:
    staff = row.get("staff", True)
    ttl = STAFF_TTL if staff else CLIENT_ABS
    async with use_tenant(row["tenant_id"]):
        await wrap(store).sessions.update_one({"session_id": row["session_id"]}, {"$set": {
            "pending_mfa": False, "mfa_verified": True, "expires_at": now() + ttl,
            "idle_s": None if staff else int(CLIENT_IDLE.total_seconds()), "last_seen_at": now()}})
    return int(ttl.total_seconds()), "/o" if staff else "/c"


async def revoke_user_sessions(store, tenant_id: str, user_id: str, keep: str | None = None) -> int:
    async with use_tenant(tenant_id):
        q = {"user_id": user_id, "revoked_at": None}
        if keep:
            q["session_id"] = {"$ne": keep}
        r = await wrap(store).sessions.update_many(q, {"$set": {"revoked_at": now()}})
    return r.modified_count


def new_recovery_codes() -> tuple[list[str], list[dict]]:
    codes = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(10)]
    return codes, [{"h": h(c), "used_at": None} for c in codes]


# ---- tokens (invite / reset) ----------------------------------------------
async def issue_token(store, *, kind: str, tenant_id: str, email: str, ttl: timedelta, **fields) -> tuple[str, dict]:
    token = secrets.token_urlsafe(32)
    doc = {"token_id": str(uuid.uuid4()), "kind": kind, "tenant_id": tenant_id, "email": email,
           "token_hash": h(token), "expires_at": now() + ttl, "used_at": None, "revoked_at": None,
           "created_at": now(), **fields}
    await wrap(store).system("auth_tokens").insert_one(doc)
    return token, doc


async def live_token(store, kind: str, token: str) -> dict | None:
    row = await wrap(store).system("auth_tokens").find_one(
        {"kind": kind, "token_hash": h(token or ""), "used_at": None, "revoked_at": None})
    if row is None or _aware(row["expires_at"]) < now():
        return None
    return row


async def use_token(store, row: dict) -> bool:
    r = await wrap(store).system("auth_tokens").update_one(
        {"token_id": row["token_id"], "used_at": None}, {"$set": {"used_at": now()}})
    return r.modified_count == 1


async def create_invite(store, *, tenant_id: str, email: str, name: str, role: str,
                        client_id: str | None, created_by: str, bootstrap: bool = False) -> tuple[str, dict]:
    email = email.strip().lower()
    if role not in STAFF_ROLES and role not in CLIENT_ROLES:
        raise HTTPException(status_code=422, detail="Pick a valid role.")
    if role in CLIENT_ROLES and not client_id:
        raise HTTPException(status_code=422, detail="Pick the client this person belongs to.")
    other, other_tid = await find_user(store, email)
    if other and other_tid != tenant_id:
        raise HTTPException(status_code=409, detail="That email already belongs to another brokerage.")
    if other and other["kind"] != ("client" if role in CLIENT_ROLES else "operator"):
        raise HTTPException(status_code=409, detail="That email is already used for a different kind of account.")
    await wrap(store).system("auth_tokens").update_many(
        {"kind": "invite", "tenant_id": tenant_id, "email": email, "used_at": None, "revoked_at": None},
        {"$set": {"revoked_at": now()}})
    return await issue_token(store, kind="invite", tenant_id=tenant_id, email=email, ttl=INVITE_TTL,
                             name=(name or "").strip() or (other or {}).get("display_name") or email.split("@")[0],
                             role=role, client_id=client_id if role in CLIENT_ROLES else None,
                             created_by=created_by, bootstrap=bootstrap)


async def apply_invite(store, inv: dict, password: str) -> dict:
    """Create/refresh the user and grant the invited role. Fresh TOTP is required afterwards."""
    tid, email, role = inv["tenant_id"], inv["email"], inv["role"]
    safe = wrap(store)
    async with use_tenant(tid):
        user = await safe.users.find_one({"email": email})
        fields = {"password_hash": passwords.hash_password(password), "password_set_at": now(),
                  "mfa_secret": None, "recovery_codes": [], "disabled": False}
        if user is None:
            user = {"user_id": str(uuid.uuid4()), "email": email, "display_name": inv["name"],
                    "kind": "client" if role in CLIENT_ROLES else "operator", "created_at": now()}
            await safe.users.insert_one({**user, **fields})
        else:
            await safe.users.update_one({"user_id": user["user_id"]}, {"$set": fields})
        uid = user["user_id"]
        if role in CLIENT_ROLES:
            cid = inv["client_id"]
            await safe.user_client_links.update_one(
                {"user_id": uid, "client_id": cid},
                {"$set": {"role": CLIENT_ROLES[role], "archived_at": None}, "$setOnInsert": {"user_id": uid, "client_id": cid}},
                upsert=True)
            if not await safe.memberships.find_one({"user_id": uid, "client_id": cid, "role": role, "revoked_at": None}):
                await safe.memberships.update_one(
                    {"user_id": uid, "client_id": cid, "role": role},
                    {"$set": {"authority": ROLE_AUTHORITY[role], "revoked_at": None, "revoked_by": None},
                     "$setOnInsert": {"membership_id": str(uuid.uuid4()), "user_id": uid, "client_id": cid, "role": role,
                                      "created_at": now(), "created_by": inv.get("created_by")}},
                    upsert=True)
        else:
            b = await safe.brokerages.find_one({})
            await safe.user_brokerage_roles.update_one(
                {"user_id": uid, "role": role},
                {"$set": {"active": True, "brokerage_id": b["brokerage_id"]}, "$setOnInsert": {"user_id": uid, "role": role}},
                upsert=True)
        return await safe.users.find_one({"user_id": uid})


# ---- auth email (transactional; separate switch from alert email) ---------
COPY = {
    "invite": ("You're invited to {brand}", "You've been invited to {brand} as {role}. Use the button to set your "
               "password and turn on your authenticator app. The link works once and expires in 72 hours.", "Accept invite"),
    "reset": ("Reset your {brand} password", "Someone asked to reset your password. Use the button to choose a new one. "
              "The link works once and expires in 1 hour. If this wasn't you, ignore this email.", "Choose a new password"),
    "mfa_reset": ("Your {brand} authenticator was reset", "Your authenticator app was reset by your brokerage. You'll set it "
                  "up again next time you sign in. If you didn't expect this, contact your broker.", "Sign in"),
}


def auth_email_enabled() -> bool:
    return (os.environ.get("EMAIL_AUTH_ENABLED") or "true").strip().lower() == "true"


def render_auth(kind: str, link: str, settings: dict, name: str, role: str = "") -> tuple[str, str, str]:
    brand, colour = settings["sender_name"] or "brokAId", settings["brand_colour"]
    subject, line, button = (s.format(brand=brand, role=role) for s in COPY[kind])
    first = escape((name or "").split(" ")[0] or "there")
    text = f"Hi {first},\n\n{line}\n\n{button}: {link}\n\nSent by {brand}. We never ask for passwords or card details by email."
    html = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F4F6FA;padding:24px 0">'
        '<tr><td align="center"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:560px;background:#FFFFFF;border-radius:12px;font-family:Arial,Helvetica,sans-serif;color:#0B1B3A">'
        f'<tr><td style="padding:20px 28px;border-bottom:3px solid {colour};font-size:15px;font-weight:bold">{escape(brand)}</td></tr>'
        f'<tr><td style="padding:24px 28px 8px;font-size:20px;font-weight:bold">{escape(subject)}</td></tr>'
        f'<tr><td style="padding:0 28px 20px;font-size:15px;line-height:22px">Hi {first},<br>{escape(line)}</td></tr>'
        f'<tr><td style="padding:0 28px 28px"><a href="{escape(link)}" style="display:inline-block;background:{colour};'
        f'color:#FFFFFF;text-decoration:none;padding:12px 22px;border-radius:999px;font-size:14px;font-weight:bold">{escape(button)}</a></td></tr>'
        '<tr><td style="padding:16px 28px;border-top:1px solid #E3E8F0;font-size:12px;color:#5B6B85">'
        f'Sent by {escape(brand)}. We never ask for passwords or card details by email.</td></tr></table></td></tr></table>')
    return subject, html, text


async def send_auth_email(store, tenant_id: str, *, kind: str, to: str, name: str, link: str, role: str = "", send=None) -> str:
    if not auth_email_enabled():
        return "skipped:auth_email_disabled"
    settings = await ec.tenant_settings(store, tenant_id)
    addr, reason = ec.resolve_address({"email": to}, settings["allowlist"])
    if addr is None:
        return reason
    subject, html, text = render_auth(kind, link, settings, name, role)
    try:
        mid = await (send or ec.resend_send)(to=addr, subject=subject, html=html, text=text,
                                             from_name=settings["sender_name"] or "brokAId", reply_to=settings["reply_to"])
        return f"sent:{mid}"
    except Exception as e:
        log.warning("auth email failed (%s): %s", kind, type(e).__name__)
        return "failed"


def invite_url(token: str) -> str:
    return f"{base_url()}/invite/{token}"


def reset_url(token: str) -> str:
    return f"{base_url()}/reset/{token}"


# ---- bootstrap the first Operator -----------------------------------------
async def has_active_operator(store, tenant_id: str) -> bool:
    safe = wrap(store)
    async with use_tenant(tenant_id):
        async for r in safe.user_brokerage_roles.find({"role": "operator", "active": True}):
            u = await safe.users.find_one({"user_id": r["user_id"]})
            if u and u.get("password_set_at") and u.get("mfa_secret") and not u.get("disabled"):
                return True
    return False


async def bootstrap_operator(store, send=None) -> str:
    email = (os.environ.get("BOOTSTRAP_OPERATOR_EMAIL") or "").strip().lower()
    slug = (os.environ.get("BOOTSTRAP_TENANT_SLUG") or "").strip()
    if not email:
        return "skipped:not_configured"
    if not slug:
        log.warning("BOOTSTRAP_OPERATOR_EMAIL is set but BOOTSTRAP_TENANT_SLUG is not; skipping")
        return "skipped:no_tenant_slug"
    tenant = await wrap(store).system("tenants").find_one({"slug": slug})
    if tenant is None:
        return "skipped:unknown_tenant"
    tid = tenant["tenant_id"]
    if await has_active_operator(store, tid):
        return "skipped:operator_exists"
    open_inv = await wrap(store).system("auth_tokens").find_one(
        {"kind": "invite", "tenant_id": tid, "bootstrap": True, "used_at": None, "revoked_at": None,
         "expires_at": {"$gt": now()}})
    if open_inv:
        return "skipped:invite_open"
    token, doc = await create_invite(store, tenant_id=tid, email=email, name="", role="operator",
                                     client_id=None, created_by="bootstrap", bootstrap=True)
    status = await send_auth_email(store, tid, kind="invite", to=email, name=doc["name"],
                                   link=invite_url(token), role="Operator", send=send)
    await ledger(store, tid, "InviteCreated", {"kind": "system", "id": "bootstrap", "name": "bootstrap"},
                 {"invite_id": doc["token_id"], "role": "operator", "bootstrap": True, "email_status": status.split(":")[0]})
    log.info("bootstrap operator invite issued for tenant %s (email %s)", slug, status.split(":")[0])
    return "issued"
