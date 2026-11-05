"""SEC-001: the one place that decides dev vs production. Fails closed: unset/unknown ENVIRONMENT means production."""
from __future__ import annotations

import logging
import os

from fastapi import HTTPException, Request

DEV_ENVS = frozenset({"development", "preview", "test"})
DEMO_SLUGS = frozenset({"harbourline", "import-test", "ai-eval"})
REAL_SLUGS = frozenset({"burmac"})  # R3.10: never demo, even if flagged
log = logging.getLogger("brokaid.devmode")


# R3.10: the one list of preview hosts. Dev mode needs one of these as APP_BASE_URL and as the served host.
PREVIEW_HOSTS = frozenset({"localhost", "127.0.0.1", "testserver"})  # testserver: in-process test client only
PREVIEW_SUFFIXES = (".preview.emergentagent.com", ".preview.emergentcf.cloud")  # second: the internal Cloudflare→cluster Host on preview
_forced_off: str | None = None  # set at boot when dev mode must not run (e.g. real tenant data present)


def environment() -> str:
    e = (os.environ.get("ENVIRONMENT") or "").strip().lower()
    return e if e in DEV_ENVS else "production"


def is_production() -> bool:
    return environment() == "production"


def _host(v: str) -> str:
    v = (v or "").strip().lower()
    if "://" in v:
        from urllib.parse import urlparse
        return (urlparse(v).hostname or "").lower()
    return v.rsplit(":", 1)[0] if v.count(":") == 1 else v


def is_preview_host(host: str | None) -> bool:
    h = _host(host or "")
    return bool(h) and (h in PREVIEW_HOSTS or h.endswith(PREVIEW_SUFFIXES))


def base_host() -> str:
    return _host(os.environ.get("APP_BASE_URL") or "")


def _dev_requested() -> bool:
    return not is_production() and (os.environ.get("AUTH_DEV_MODE") or "").strip().lower() == "true"


def dev_mode() -> bool:
    """Dev ENVIRONMENT + AUTH_DEV_MODE + a preview APP_BASE_URL host, and not switched off at boot."""
    return _dev_requested() and is_preview_host(base_host()) and _forced_off is None


def effective_mode() -> str:
    return "preview" if dev_mode() else "production"


def served_hosts(request) -> list[str]:
    """Host plus every X-Forwarded-Host value. A spoofed header can only add names; the real public one is still in the list."""
    vals = [request.headers.get("host") or ""] + (request.headers.get("x-forwarded-host") or "").split(",")
    return [h for h in (_host(v) for v in vals) if h]


def served_host(request) -> str:
    hs = served_hosts(request)
    return (request.headers.get("x-forwarded-host") or "").split(",")[0].strip().lower().rsplit(":", 1)[0] or (hs[0] if hs else "")


def preview_request(request) -> bool:
    hs = served_hosts(request)
    return bool(hs) and all(is_preview_host(h) for h in hs)


def require_preview_request(request: Request) -> None:
    """Request-time backstop on every dev/legacy/demo sign-in route: 404 unless dev mode and served from a preview host."""
    if not dev_mode() or not preview_request(request):
        raise HTTPException(status_code=404, detail="not_found")


def config_warnings() -> list[str]:
    out = []
    env, raw_dev = environment(), (os.environ.get("AUTH_DEV_MODE") or "").strip().lower() == "true"
    if env != "production" and not is_preview_host(base_host()):
        out.append(f"ENVIRONMENT={env} but APP_BASE_URL host '{base_host() or 'unset'}' is not a preview host: running in production mode.")
    if raw_dev and not dev_mode():
        out.append("AUTH_DEV_MODE=true is ignored: " + (_forced_off or "not a preview environment") + ".")
    if not dev_mode() and "*" in [o.strip() for o in (os.environ.get("CORS_ORIGINS") or "").split(",")]:
        out.append("CORS_ORIGINS='*' is ignored in production; allowed origins are APP_BASE_URL plus EXTRA_ALLOWED_ORIGINS.")
    if not dev_mode() and not base_host():
        out.append("APP_BASE_URL is not set: cookies, CSRF origins and email links need it.")
    return out


def is_demo_row(t: dict | None) -> bool:
    """Demo/test tenants only. BurMac, wizard-created brokerages and the platform tenant are always real."""
    if not t or t.get("kind") in ("platform", "brokerage") or t.get("slug") in REAL_SLUGS:
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


async def require_demo_seed(db, tenant_id: str | None) -> None:
    """Demo seeds only ever write into a demo/test tenant — never BurMac, wizard brokerages or the platform."""
    if not await is_demo_tenant(db, tenant_id):
        raise RuntimeError(f"refusing to seed demo data into non-demo tenant {tenant_id}")


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
    """Dev mode must never run next to real brokerage data. R3.10: switch dev mode off and log CRITICAL instead of crash-looping."""
    global _forced_off
    _forced_off = None  # recomputed on every boot
    for w in config_warnings():
        log.critical("CONFIG: %s", w)
    if not dev_mode():
        return
    problems = await real_tenant_problems(store)
    if problems:
        _forced_off = "real tenant data is present (" + "; ".join(problems) + ")"
        log.critical("DEV MODE REFUSED: AUTH_DEV_MODE is on but %s. Running in production mode; dev routes answer 404.", _forced_off)


async def require_import_allowed(store, tenant_id: str) -> None:
    """iAdviser imports into a real tenant run only in production."""
    if not is_production() and not await is_demo_tenant(store, tenant_id):
        raise HTTPException(status_code=403, detail="Imports into a real brokerage only run in production. Use a test tenant on preview.")
