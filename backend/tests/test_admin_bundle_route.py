"""Phase 12.6 — /api/_admin/bundle route guards.

Validates:
  * Route is NOT mounted when AUTH_DEV_MODE=false → 404.
  * Route IS mounted when AUTH_DEV_MODE=true + ADMIN_BUNDLE_PATH set.
  * Wrong token → 410 Gone.
  * Correct token → 200 with correct headers, single-use.
  * Second correct-token request → 410 Gone (token burned).
  * Every hit emits an AdminBundleDownloadRequested ledger event under
    the `brokaid-ops` tenant.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.asyncio


DEV_BUNDLE = "brokaid-test-bundle.txt"


def _prepare_bundle_file(tmp_path: Path) -> Path:
    p = tmp_path / DEV_BUNDLE
    p.write_bytes(b"pretend this is a git bundle\n")
    return p


async def test_route_not_mounted_when_auth_dev_mode_false(
    pool, monkeypatch, tmp_path,
):
    """Guard: with AUTH_DEV_MODE=false the /api/_admin/bundle path
    must return 404 (route not registered)."""
    bundle = _prepare_bundle_file(tmp_path)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ADMIN_BUNDLE_PATH", str(bundle))

    from app.main import create_app
    app = create_app(pool_override=pool, seed_dev_users=False)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as client:
            r = await client.get("/api/_admin/bundle?token=anything")
            assert r.status_code == 404, (
                f"expected 404 when AUTH_DEV_MODE=false, got {r.status_code}"
            )


async def _mount_dev_app(pool, monkeypatch, tmp_path):
    bundle = _prepare_bundle_file(tmp_path)
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ADMIN_BUNDLE_PATH", str(bundle))
    monkeypatch.setenv("ADMIN_BUNDLE_TTL_MINUTES", "30")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    from app.main import create_app
    app = create_app(pool_override=pool, seed_dev_users=False)
    # Force the ADMIN_BUNDLE mount at app construction time — main.py
    # already does it, but we double-check state is present.
    state = getattr(app.state, "admin_bundle", None)
    assert state is not None, "admin bundle state missing after mount"
    return app, bundle


async def test_bad_token_returns_410_and_emits_event(pool, monkeypatch, tmp_path):
    app, _ = await _mount_dev_app(pool, monkeypatch, tmp_path)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as client:
            r = await client.get("/api/_admin/bundle?token=wrong-token-value")
            assert r.status_code == 410

    # The ops tenant must have been auto-created by the ledger emitter.
    # The event itself is RLS-guarded — we bind the tenant GUC inside a
    # transaction to read it back.
    async with pool.acquire() as c:
        tenant_row = await c.fetchrow(
            "SELECT tenant_id::text AS tid FROM tenants WHERE slug=$1",
            "brokaid-ops",
        )
        assert tenant_row is not None, "brokaid-ops tenant must be auto-created"
        tid = tenant_row["tid"]
        assert tid, f"tenant_id must not be empty; got {tid!r}"
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tid)
            events = await c.fetch(
                "SELECT payload FROM events "
                "WHERE tenant_id=$1::uuid AND type=$2",
                tid, "AdminBundleDownloadRequested",
            )
    assert len(events) >= 1, "expected at least one bundle-request event"
    import json
    flags = [
        (json.loads(e["payload"]) if isinstance(e["payload"], str) else e["payload"])["ok"]
        for e in events
    ]
    assert False in flags, "at least one event must record ok=False on bad token"


async def test_correct_token_serves_file_once(pool, monkeypatch, tmp_path):
    app, bundle = await _mount_dev_app(pool, monkeypatch, tmp_path)
    state = app.state.admin_bundle
    # Re-derive the raw token: not possible (we only stored the hash).
    # Read it from log record via app.state's private handle: we regenerate
    # the state with a known raw token by rotating.
    import secrets
    import hashlib as _hl
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    raw = secrets.token_urlsafe(32)
    state.token_sha256 = _hl.sha256(raw.encode("ascii")).digest()
    state.expires_at = _dt.now(_tz.utc) + _td(minutes=5)
    state.consumed = False

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as client:
            r = await client.get(f"/api/_admin/bundle?token={raw}")
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("application/octet-stream")
            assert bundle.name in r.headers.get("content-disposition", "")
            assert r.content == bundle.read_bytes()

            # Second hit must be 410 (state cleared).
            r2 = await client.get(f"/api/_admin/bundle?token={raw}")
            assert r2.status_code == 410
