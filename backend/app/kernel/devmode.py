"""SEC-001: the one place that decides dev vs production. Fails closed: unset/unknown ENVIRONMENT means production."""
from __future__ import annotations

import logging
import os

from fastapi import HTTPException

DEV_ENVS = frozenset({"development", "preview", "test"})
DEMO_SLUGS = frozenset({"harbourline", "import-test", "ai-eval"})
log = logging.getLogger("brokaid.devmode")


def environment() -> str:
    e = (os.environ.get("ENVIRONMENT") or "").strip().lower()
    return e if e in DEV_ENVS else "production"


def is_production() -> bool:
    return environment() == "production"


def dev_mode() -> bool:
    return not is_production() and (os.environ.get("AUTH_DEV_MODE") or "").strip().lower() == "true"


def is_demo_row(t: dict | None) -> bool:
    """Demo/test tenants only. BurMac, wizard-created brokerages and the platform tenant are always real."""
    if not t or t.get("kind") in ("platform", "brokerage") or t.get("slug") == "burmac":
        return False
    slug = t.get("slug") or ""
    return slug in DEMO_SLUGS or slug.startswith("ci-") or t.get("test") is True or t.get("demo") is True


async def is_demo_tenant(store, tenant_id: str | None) -> bool:
    if not tenant_id:
        return False
    # tenant-safe: system-only — tenant registry read.
    return is_demo_row(await store.tenants.find_one({"tenant_id": tenant_id}))


async def require_dev_tenant(store, tenant_id: str | None, role: str | None = None) -> None:
    """Dev shortcuts (impersonation, legacy logins): dev mode + demo tenant + never platform_admin, else 404."""
    if not dev_mode() or role == "platform_admin" or not await is_demo_tenant(store, tenant_id):
        raise HTTPException(status_code=404, detail="not_found")


async def real_tenant_problems(store) -> list[str]:
    out = []
    async for t in store.tenants.find({}, {"_id": 0, "tenant_id": 1, "slug": 1, "kind": 1, "status": 1, "test": 1, "demo": 1}):
        if is_demo_row(t) or t.get("kind") == "platform":  # platform tenant is live by design and holds no client data
            continue
        tid = t["tenant_id"]
        if t.get("status") == "live":
            out.append(f"{t.get('slug')}: status=live")
        if await store.import_runs.count_documents({"tenant_id": tid}, limit=1) or await store.clients.count_documents(
                {"tenant_id": tid, "imported_from": {"$ne": None}, "test": {"$ne": True}}, limit=1):
            out.append(f"{t.get('slug')}: holds imported data")
    return out


async def boot_guard(store) -> None:
    """Dev mode must never run next to real brokerage data."""
    if not dev_mode():
        return
    problems = await real_tenant_problems(store)
    if problems:
        msg = ("REFUSING TO START: AUTH_DEV_MODE is on but real tenant data is present (" + "; ".join(problems) +
               "). Real brokerage data must never be loaded on preview. Turn dev mode off or use a clean database.")
        log.critical(msg)
        raise RuntimeError(msg)


async def require_import_allowed(store, tenant_id: str) -> None:
    """iAdviser imports into a real tenant run only in production."""
    if not is_production() and not await is_demo_tenant(store, tenant_id):
        raise HTTPException(status_code=403, detail="Imports into a real brokerage only run in production. Use a test tenant on preview.")
