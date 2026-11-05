"""R3.10: read-only exposure report. Shows whether anyone used dev sign-in or demo actions since a given time, across demo/test tenants."""
from __future__ import annotations

from datetime import datetime, timezone

from app.db.tenant_safe import wrap
from app.kernel import devmode
from app.kernel.tenant_ctx import use_tenant

DEMO_EVENTS = ("DemoResetExecuted", "SandboxReset", "RenewalDemoReset", "DemoCredentialsDisabled")
LOGIN_EVENTS = ("LoginSucceeded", "OperatorLogin", "ClientMagicLinkVerified", "MfaEnrolled")


def _iso(v):
    return v.replace(tzinfo=v.tzinfo or timezone.utc).isoformat() if isinstance(v, datetime) else v


async def build(store, since: datetime) -> dict:
    tenants = [t async for t in store.tenants.find({}, {"_id": 0, "tenant_id": 1, "slug": 1, "test": 1, "demo": 1, "kind": 1})  # tenant-safe: system-only
               if devmode.is_demo_row(t)]
    sessions, events = [], []
    for t in tenants:
        safe = wrap(store)
        async with use_tenant(t["tenant_id"]):
            emails = {u["user_id"]: u.get("email") async for u in safe.users.find({}, {"user_id": 1, "email": 1})}
            async for s in safe.sessions.find({"created_at": {"$gte": since}}).sort("created_at", -1).limit(500):
                sessions.append({"tenant": t["slug"], "at": _iso(s["created_at"]), "via": s.get("via") or "not marked as dev sign-in (sessions before R3.10 are never marked)",
                                 "persona": s.get("persona_email") or emails.get(s["user_id"]), "role": s.get("active_role"),
                                 "ip": s.get("ip"), "served_host": s.get("served_host")})
            async for e in safe.events.find({"type": {"$in": [*DEMO_EVENTS, *LOGIN_EVENTS]}, "created_at": {"$gte": since.replace(tzinfo=None)}}).sort("seq", -1).limit(500):
                p = e.get("payload") or {}
                events.append({"tenant": t["slug"], "at": e.get("ts"), "type": e["type"], "actor": (e.get("actor") or {}).get("id"),
                               "persona": emails.get(p.get("requested_by") or (e.get("actor") or {}).get("id")), "scope": p.get("scope"), "ip": p.get("ip")})
    mfa = []
    for t in tenants:
        async with use_tenant(t["tenant_id"]):
            for em in {u["email"] async for u in wrap(store).users.find({}, {"email": 1}) if u.get("email")}:
                i = await wrap(store).system("auth_identities").find_one({"email": em}, {"mfa_enrolled_at": 1, "password_disabled": 1})
                if i and i.get("mfa_enrolled_at") and i["mfa_enrolled_at"].replace(tzinfo=i["mfa_enrolled_at"].tzinfo or timezone.utc) >= since:
                    mfa.append({"tenant": t["slug"], "persona": em, "at": _iso(i["mfa_enrolled_at"])})
    return {"since": since.isoformat(), "tenants": [t["slug"] for t in tenants], "sessions": sessions, "events": events,
            "mfa_enrolments": mfa,
            "dev_password_logins_note": "Since R3.10 the dev password is refused outside dev mode. Earlier password sign-ins appear under events (LoginSucceeded/OperatorLogin) for these personas.",
            "ip_note": "Sessions record IP only from R3.10 on; earlier rows show none."}
