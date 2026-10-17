"""Phase R5c-2b — boot-flip parity pins.

Locks the observable behaviour of `create_app()` under both the
Mongo-primary default and the legacy-PG opt-in. The R5c-2b flip
makes Mongo required at startup; Postgres becomes optional under
`BROKAID_LEGACY_PG=true`.

Coverage
--------
- Boot pin: `create_app(mongo_db_override=mongo_db, seed_dev_users=False)`
  under `MONGO_URL` set + `MONGO_URL` unset + `BROKAID_LEGACY_PG`
  unset — no exception, `app.state.pool is None`, `app.state.mongo_db`
  is the injected Motor db.
- Healthz pin: `/api/healthz` under Mongo-only returns 200 with
  `mongo.ok == True` and `postgres is None`.
- Middleware pin: TenantContextMiddleware under Mongo-only mode
  reads the session via `identity.read_session(mongo_db, cookie)`
  and binds `current_tenant_id` correctly.
- Legacy-PG pin: `create_app(pool_override, mongo_db_override,
  seed_dev_users=False)` boots both stores — `app.state.pool` is
  the injected pool, `app.state.mongo_db` is the injected db,
  and `/api/healthz` reports `postgres.ok == True`.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from app.auth.sessions import COOKIE_NAME


pytestmark = pytest.mark.asyncio


CI_A_ID = "b21fb26d-42a1-587a-b2b3-b1e6b9c48e6a"


# ---- Pin 1: Mongo-only boot — pool is None, mongo_db is bound -------
async def test_pin_r5c2b_boot_mongo_only(monkeypatch, mongo_db):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("BROKAID_LEGACY_PG", raising=False)
    monkeypatch.delenv("BROKAID_IDENTITY", raising=False)
    monkeypatch.delenv("BROKAID_LEDGER", raising=False)
    monkeypatch.delenv("BROKAID_DOMAIN", raising=False)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    monkeypatch.setenv("ENVIRONMENT", "development")

    from app.main import create_app
    app = create_app(mongo_db_override=mongo_db, seed_dev_users=False)
    async with app.router.lifespan_context(app):
        assert app.state.pool is None
        assert app.state.postgres_version is None
        assert app.state.mongo_db is mongo_db
        assert app.state.mongo_ready is True


# ---- Pin 2: /api/healthz under Mongo-only — mongo ok, postgres null -
async def test_pin_r5c2b_healthz_mongo_only(monkeypatch, mongo_db):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("BROKAID_LEGACY_PG", raising=False)
    monkeypatch.delenv("BROKAID_IDENTITY", raising=False)
    monkeypatch.delenv("BROKAID_LEDGER", raising=False)
    monkeypatch.delenv("BROKAID_DOMAIN", raising=False)
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    monkeypatch.setenv("ENVIRONMENT", "development")

    from app.main import create_app
    app = create_app(mongo_db_override=mongo_db, seed_dev_users=False)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as client:
            r = await client.get("/api/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "brokaid-api"
    assert isinstance(body["mongo"], dict)
    assert body["mongo"]["ok"] is True
    assert body["postgres"] is None


# ---- Pin 3: TenantContextMiddleware under Mongo-only binds tenant ---
async def test_pin_r5c2b_middleware_mongo_only_binds_tenant(
    monkeypatch, mongo_db,
):
    """Seed a session on Mongo, hit an endpoint that requires the
    session, verify the middleware resolved the cookie against the
    Mongo store (not PG — `app.state.pool is None`).
    """
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("BROKAID_LEGACY_PG", raising=False)
    monkeypatch.delenv("BROKAID_IDENTITY", raising=False)
    monkeypatch.delenv("BROKAID_LEDGER", raising=False)
    monkeypatch.delenv("BROKAID_DOMAIN", raising=False)
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")

    # Seed a tenant + operator + session directly on Mongo.
    from app.db.tenant_safe import wrap
    from app.kernel import identity
    from app.kernel.tenant_ctx import use_tenant
    from app.seeds.ci_deterministic import seed_ci_deterministic_mongo
    await seed_ci_deterministic_mongo(mongo_db)

    safe = wrap(mongo_db)
    op_user = str(uuid.uuid4())
    async with use_tenant(CI_A_ID):
        await safe.users.insert_one({
            "user_id": op_user, "email": "op@ci.example",
            "display_name": "Op", "kind": "operator",
            "password_hash": None, "mfa_secret": None,
        })
        await safe.user_brokerage_roles.insert_one({
            "user_id": op_user, "brokerage_id": str(uuid.uuid4()),
            "role": "principal", "active": True,
        })

    sess = await identity.create_session(
        mongo_db, user_id=op_user, tenant_id=CI_A_ID,
        active_role="principal", mfa_verified=True,
    )

    from app.main import create_app
    app = create_app(mongo_db_override=mongo_db, seed_dev_users=False)
    async with app.router.lifespan_context(app):
        assert app.state.pool is None  # No PG in this boot.
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as client:
            r = await client.get(
                "/api/o/queue",
                cookies={COOKIE_NAME: sess.session_id},
            )
    # 200 means: cookie was accepted by middleware, tenant was bound,
    # queue endpoint dispatched via Mongo (no pool needed).
    assert r.status_code == 200, r.text
    assert r.json()["count"] >= 0


# ---- Pin 4: Legacy-PG boot — both stores online, healthz reports pg -
async def test_pin_r5c2b_boot_legacy_pg(monkeypatch, pool, mongo_db):
    monkeypatch.setenv("BROKAID_LEGACY_PG", "true")
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    monkeypatch.setenv("ENVIRONMENT", "development")

    from app.main import create_app
    app = create_app(
        pool_override=pool, mongo_db_override=mongo_db, seed_dev_users=False,
    )
    async with app.router.lifespan_context(app):
        assert app.state.pool is pool
        assert app.state.mongo_db is mongo_db
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver",
        ) as client:
            r = await client.get("/api/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["mongo"]["ok"] is True
    assert isinstance(body["postgres"], dict)
    assert body["postgres"]["ok"] is True
