"""R3.10: outside dev mode, demo users' well-known dev password is disabled and MFA enrolled on a public host is revoked.
Demo/test tenants only, never real users, idempotent, ledgered as DemoCredentialsDisabled."""
from __future__ import annotations

import asyncio
import logging

from app.auth import passwords
from app.kernel import auth_flows as af, devmode
from app.kernel.renewals import _event
from app.seeds.seed_users import DEV_PASSWORD

log = logging.getLogger("brokaid.demo_credentials")


async def _demo_tenants(store) -> set[str]:
    return {t["tenant_id"] async for t in store.tenants.find({}) if devmode.is_demo_row(t)}  # tenant-safe: system-only — tenant registry scan


def _dev_pw(h: str | None) -> bool:
    try:
        return bool(h) and passwords.verify_password(h, DEV_PASSWORD)
    except Exception:  # noqa: BLE001
        return False


async def disable_all(store) -> dict:
    """Disable dev-password demo users. On a public host, also revoke their MFA. Skips any email that has a user in a real tenant."""
    if devmode.dev_mode():
        return {"skipped": "dev_mode"}
    demo = await _demo_tenants(store)
    public = not devmode.is_preview_host(devmode.base_host())
    users = [u async for u in store.users.find({"tenant_id": {"$in": list(demo)}}, {"_id": 0, "user_id": 1, "email": 1, "tenant_id": 1,  # tenant-safe: system-only — demo tenants only
                                                                                  "password_hash": 1, "password_disabled": 1})]
    real = {u["email"] async for u in store.users.find({"email": {"$in": [u["email"] for u in users]}, "tenant_id": {"$nin": list(demo)}},  # tenant-safe: system-only
                                                        {"email": 1})}
    by_tenant: dict[str, dict] = {}
    for u in users:
        if u["email"] in real:
            continue
        ident = await af.identity(store, u["email"]) or {}
        hit = await asyncio.to_thread(_dev_pw, u.get("password_hash")) or await asyncio.to_thread(_dev_pw, ident.get("password_hash"))
        if not hit:
            continue
        rec = by_tenant.setdefault(u["tenant_id"], {"disabled": [], "mfa_revoked": []})
        if not u.get("password_disabled"):
            await store.users.update_one({"tenant_id": u["tenant_id"], "user_id": u["user_id"]}, {"$set": {"password_disabled": True}})  # tenant-safe: system-only
            rec["disabled"].append(u["email"])
        if ident and not ident.get("password_disabled"):
            await af.set_identity(store, u["email"], {"password_disabled": True})
        if public and (ident.get("mfa_secret") or ident.get("recovery_codes")):
            await af.set_identity(store, u["email"], {"mfa_secret": None, "recovery_codes": [], "mfa_enrolled_at": None})
            rec["mfa_revoked"].append(u["email"])
    for tid, rec in by_tenant.items():
        if rec["disabled"] or rec["mfa_revoked"]:
            await _event(store, tid, "DemoCredentialsDisabled", {**rec, "public_host": public, "base_host": devmode.base_host()},
                         f"demo_creds_disabled:{tid}:{','.join(sorted(rec['disabled'] + rec['mfa_revoked']))}", "system", "operator")
            log.critical("demo credentials disabled in tenant %s: %s", tid, rec)
    return {"tenants": {t: r for t, r in by_tenant.items() if r["disabled"] or r["mfa_revoked"]}}
