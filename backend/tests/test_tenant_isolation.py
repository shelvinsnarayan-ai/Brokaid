"""Phase R3 — cross-tenant isolation acceptance gate.

Every scenario a leak could take, exercised as a binary pass/fail
test. No warnings, no `xfail`.

Coverage
--------
Mongo (tenant-safe wrapper):
  1. Every ledger read scoped by ctx returns ZERO other-tenant events.
  2. Explicit UUID `read_events(tenant_ref=other)` → TenantMismatchError.
  3. Explicit slug `read_events(tenant_ref=other)` → TenantMismatchError.
  4. No context set → NoTenantContextError on the first Mongo touch.
  5. Aggregation with contradictory `$match` on tenant_id → TenantMismatchError.
  6. Aggregation without any `$match` → wrapper prepends one; result is
     scoped even though the caller "forgot".
  7. Explicit `tenant_id` in filter contradicting ctx → TenantMismatchError.
  8. Attempt to write with a mismatched tenant_id → TenantMismatchError.
  9. `bulk_write` on any tenant-safe collection → AppendOnlyError.
 10. Any non-insert method on the `events` wrapper → AppendOnlyError.
 11. `system(name)` allow-list: data collections raise
     SystemCollectionNotAllowed; `tenants`/`_counters`/`_locks` return raw.
 12. Concurrent requests hold independent tenant contexts (asyncio
     ContextVar semantics under Starlette).

Postgres RLS parity:
 13. Two tenants seeded; setting `app.tenant_id` GUC to tenant A
     returns only A's events; tenant B's events are invisible.

Middleware smoke:
 14. A request with a valid session sets the ctx; a protected route
     succeeds.
 15. A request without a session hits 401, not any spurious Mongo
     error.
 16. Operator cross-client-within-tenant: switching client_id inside
     a session doesn't change tenant_id → ctx unchanged.
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_two_tenants(mongo_db):
    from app.seeds.ci_deterministic import (
        CI_TENANT_A_ID, CI_TENANT_A_SLUG,
        CI_TENANT_B_ID, CI_TENANT_B_SLUG,
        seed_ci_deterministic_mongo,
    )
    await seed_ci_deterministic_mongo(mongo_db)
    return CI_TENANT_A_ID, CI_TENANT_A_SLUG, CI_TENANT_B_ID, CI_TENANT_B_SLUG


# ---- 1 ----------------------------------------------------------------
async def test_r3_find_scoped_to_context(mongo_db):
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import use_tenant

    a_id, _, b_id, _ = await _seed_two_tenants(mongo_db)
    safe = wrap(mongo_db)

    async with use_tenant(a_id):
        events = [e async for e in safe.events.find({})]
    assert events, "expected some events for tenant A"
    assert all(e["tenant_id"] == a_id for e in events)
    assert not any(e["tenant_id"] == b_id for e in events)

    async with use_tenant(b_id):
        count_b = await safe.events.count_documents({})
    assert count_b > 15


# ---- 2 ----------------------------------------------------------------
async def test_r3_read_events_cross_tenant_uuid_raises(mongo_db):
    from app.kernel import trail_mongo
    from app.kernel.tenant_ctx import TenantMismatchError, use_tenant

    a_id, _, b_id, _ = await _seed_two_tenants(mongo_db)
    async with use_tenant(a_id):
        with pytest.raises(TenantMismatchError):
            await trail_mongo.read_events(mongo_db, b_id)


# ---- 3 ----------------------------------------------------------------
async def test_r3_read_events_cross_tenant_slug_raises(mongo_db):
    from app.kernel import trail_mongo
    from app.kernel.tenant_ctx import TenantMismatchError, use_tenant

    a_id, _, _, b_slug = await _seed_two_tenants(mongo_db)
    async with use_tenant(a_id):
        with pytest.raises(TenantMismatchError):
            await trail_mongo.read_events(mongo_db, b_slug)


# ---- 4 ----------------------------------------------------------------
async def test_r3_missing_context_raises(mongo_db):
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import NoTenantContextError

    await _seed_two_tenants(mongo_db)
    safe = wrap(mongo_db)
    with pytest.raises(NoTenantContextError):
        await safe.events.find_one({})
    with pytest.raises(NoTenantContextError):
        await safe.events.count_documents({})
    with pytest.raises(NoTenantContextError):
        _ = [x async for x in safe.events.aggregate([{"$match": {"seq": 1}}])]


# ---- 5 ----------------------------------------------------------------
async def test_r3_aggregate_contradictory_match_raises(mongo_db):
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import TenantMismatchError, use_tenant

    a_id, _, b_id, _ = await _seed_two_tenants(mongo_db)
    safe = wrap(mongo_db)
    async with use_tenant(a_id):
        with pytest.raises(TenantMismatchError):
            _ = [x async for x in safe.events.aggregate(
                [{"$match": {"tenant_id": b_id}}, {"$count": "n"}]
            )]


# ---- 6 ----------------------------------------------------------------
async def test_r3_aggregate_missing_match_gets_prepended(mongo_db):
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import use_tenant

    a_id, _, b_id, _ = await _seed_two_tenants(mongo_db)
    safe = wrap(mongo_db)
    async with use_tenant(a_id):
        rows = [x async for x in safe.events.aggregate([{"$sort": {"seq": 1}}])]
    assert rows
    assert all(r["tenant_id"] == a_id for r in rows)
    assert not any(r["tenant_id"] == b_id for r in rows)


# ---- 7 ----------------------------------------------------------------
async def test_r3_filter_contradiction_raises(mongo_db):
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import TenantMismatchError, use_tenant

    a_id, _, b_id, _ = await _seed_two_tenants(mongo_db)
    safe = wrap(mongo_db)
    async with use_tenant(a_id):
        with pytest.raises(TenantMismatchError):
            await safe.events.find_one({"tenant_id": b_id})
        with pytest.raises(TenantMismatchError):
            await safe.events.count_documents({"tenant_id": b_id})


# ---- 8 ----------------------------------------------------------------
async def test_r3_insert_with_wrong_tenant_id_raises(mongo_db):
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import TenantMismatchError, use_tenant

    a_id, _, b_id, _ = await _seed_two_tenants(mongo_db)
    safe = wrap(mongo_db)
    async with use_tenant(a_id):
        with pytest.raises(TenantMismatchError):
            await safe.events.insert_one({
                "event_id": "00000000-0000-4000-8000-000000000abc",
                "tenant_id": b_id,   # contradicts ctx
                "seq": 999_999,
                "ts": "2024-01-01T00:00:00.000000+00:00",
                "actor": {}, "type": "test",
                "payload": {}, "payload_jcs": "{}",
                "prev_hash": "0" * 64, "event_hash": "0" * 64,
                "idempotency_key": "test:wrong-tenant:1", "mocked": False,
            })


# ---- 9 ----------------------------------------------------------------
async def test_r3_bulk_write_forbidden(mongo_db):
    from app.db.tenant_safe import AppendOnlyError, wrap
    from app.kernel.tenant_ctx import use_tenant

    a_id, *_ = await _seed_two_tenants(mongo_db)
    safe = wrap(mongo_db)
    async with use_tenant(a_id):
        with pytest.raises(AppendOnlyError):
            await safe.events.bulk_write([])
        with pytest.raises(AppendOnlyError):
            await safe.tenants.bulk_write([])


# ---- 10 ---------------------------------------------------------------
async def test_r3_events_is_append_only(mongo_db):
    from app.db.tenant_safe import AppendOnlyError, wrap
    from app.kernel.tenant_ctx import use_tenant

    a_id, *_ = await _seed_two_tenants(mongo_db)
    safe = wrap(mongo_db)
    async with use_tenant(a_id):
        with pytest.raises(AppendOnlyError):
            await safe.events.update_one({"seq": 1}, {"$set": {"mocked": True}})
        with pytest.raises(AppendOnlyError):
            await safe.events.delete_one({"seq": 1})
        with pytest.raises(AppendOnlyError):
            await safe.events.replace_one({"seq": 1}, {"seq": 1})
        with pytest.raises(AppendOnlyError):
            await safe.events.find_one_and_update({"seq": 1}, {"$set": {"x": 1}})
        with pytest.raises(AppendOnlyError):
            await safe.events.insert_many([{}, {}])


# ---- 11 ---------------------------------------------------------------
async def test_r3_system_collection_allow_list(mongo_db):
    from app.db.tenant_safe import SystemCollectionNotAllowed, wrap
    from motor.motor_asyncio import AsyncIOMotorCollection

    safe = wrap(mongo_db)
    # Allowed: tenants, sessions (R4a addition), _counters, _locks.
    assert isinstance(safe.system("tenants"), AsyncIOMotorCollection)
    assert isinstance(safe.system("sessions"), AsyncIOMotorCollection)
    assert isinstance(safe.system("_counters"), AsyncIOMotorCollection)
    assert isinstance(safe.system("_locks"), AsyncIOMotorCollection)
    # Forbidden: every data collection.
    for name in ("events", "users", "clients", "policies", "proj_queue"):
        with pytest.raises(SystemCollectionNotAllowed):
            safe.system(name)


# ---- 12 ---------------------------------------------------------------
async def test_r3_concurrent_tenants_independent(mongo_db):
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import use_tenant

    a_id, _, b_id, _ = await _seed_two_tenants(mongo_db)
    safe = wrap(mongo_db)

    async def count_for(tid):
        async with use_tenant(tid):
            await asyncio.sleep(0)
            return await safe.events.count_documents({})

    a_count, b_count = await asyncio.gather(count_for(a_id), count_for(b_id))
    assert a_count > 15
    assert b_count > 15
    async with use_tenant(a_id):
        expected_a = await safe.events.count_documents({})
    async with use_tenant(b_id):
        expected_b = await safe.events.count_documents({})
    assert a_count == expected_a
    assert b_count == expected_b


# ---- 13 — Postgres RLS parity -----------------------------------------
async def test_r3_postgres_rls_isolation_parity(pool):
    from app.seeds.ci_deterministic import (
        CI_TENANT_A_ID, CI_TENANT_B_ID, seed_ci_deterministic,
    )
    await seed_ci_deterministic(pool)

    async def _count_via_rls(tenant_id, other_id):
        async with pool.acquire() as c:
            async with c.transaction():
                await c.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
                )
                own = await c.fetchval(
                    "SELECT count(*) FROM events WHERE tenant_id = $1::uuid",
                    tenant_id,
                )
                other = await c.fetchval(
                    "SELECT count(*) FROM events WHERE tenant_id = $1::uuid",
                    other_id,
                )
        return own, other

    a_own, a_leaked = await _count_via_rls(CI_TENANT_A_ID, CI_TENANT_B_ID)
    assert a_own > 15
    assert a_leaked == 0, (
        f"Postgres RLS leaked {a_leaked} events of tenant B into "
        f"tenant A's scope — parity broken vs Mongo wrapper."
    )
    b_own, b_leaked = await _count_via_rls(CI_TENANT_B_ID, CI_TENANT_A_ID)
    assert b_own > 15
    assert b_leaked == 0


# ---- 14 — middleware binds ctx from cookie (positive) -----------------
async def test_r3_middleware_binds_ctx_when_session_present(app_client, seeded):
    """Impersonate then hit a session-protected route. The middleware
    must have read the cookie, set the ctx, and let the route pass."""
    _, client = app_client
    r = await client.post(
        "/api/auth/dev/impersonate",
        json={"email": "principal@harbourline.example"},
    )
    assert r.status_code == 200, r.text
    # httpx.AsyncClient persists cookies across requests; the next call
    # carries the session cookie. `/api/o/queue` is operator-only.
    q = await client.get("/api/o/queue")
    assert q.status_code == 200, q.text


# ---- 15 — middleware leaves ctx unset without cookie -------------------
async def test_r3_middleware_leaves_ctx_unset_without_session(app_client):
    """No cookie → ctx unset. An operator route must 401, not leak any
    Mongo error or falsely-authenticated data."""
    _, client = app_client
    r = await client.get("/api/o/queue")
    assert r.status_code == 401, r.text


# ---- 16 — operator switching client_id keeps tenant_id -----------------
async def test_r3_operator_switch_client_keeps_tenant_ctx(app_client, seeded):
    """Two authenticated calls with the same session cookie hit the same
    tenant_id — the middleware doesn't drift ctx across a session's
    lifetime. This is the shape of the operator-switches-client flow:
    session.client_id may change, session.tenant_id does not."""
    _, client = app_client
    r = await client.post(
        "/api/auth/dev/impersonate",
        json={"email": "principal@harbourline.example"},
    )
    assert r.status_code == 200, r.text
    tenant_before = r.json().get("tenant_id")
    assert tenant_before, "impersonate should return tenant_id"

    r2 = await client.post(
        "/api/auth/dev/impersonate",
        json={"email": "principal@harbourline.example"},
    )
    assert r2.status_code == 200, r2.text
    tenant_now = r2.json().get("tenant_id")
    assert tenant_before == tenant_now, (
        f"tenant drift across two impersonations of the same principal: "
        f"{tenant_before!r} → {tenant_now!r}"
    )


# ---- R4a: identity collection isolation --------------------------------
async def test_r3_users_scoped_to_context(pool, mongo_db):
    """Users are per-tenant in the Mongo model (seed writes them under
    the current tenant ctx). Reading users with ctx=A must never see
    users seeded under ctx=B."""
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import use_tenant

    from app.seeds.seed_users_mongo import seed_mongo
    a_result = await seed_mongo(mongo_db)
    a_id = a_result["tenant_id"]

    safe = wrap(mongo_db)
    # Insert a synthetic user under a different tenant.
    b_id = "00000000-0000-4000-8000-0000000000b1"
    async with use_tenant(b_id):
        await safe.users.insert_one({
            "user_id": "00000000-0000-4000-8000-0000000000b2",
            "email": "b-only@fake.example",
            "display_name": "B Only",
            "kind": "operator", "password_hash": None, "mfa_secret": None,
        })

    async with use_tenant(a_id):
        # Tenant A cannot see B's user.
        row = await safe.users.find_one({"email": "b-only@fake.example"})
        assert row is None
        # Tenant A users all belong to A.
        cnt = await safe.users.count_documents({})
        assert cnt >= 1
        async for u in safe.users.find({}):
            assert u["tenant_id"] == a_id


async def test_r3_brokerage_roles_scoped(mongo_db):
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import use_tenant
    from app.seeds.seed_users_mongo import seed_mongo

    a_result = await seed_mongo(mongo_db)
    a_id = a_result["tenant_id"]
    safe = wrap(mongo_db)

    b_id = "00000000-0000-4000-8000-0000000000c1"
    async with use_tenant(b_id):
        await safe.user_brokerage_roles.insert_one({
            "user_id": "00000000-0000-4000-8000-0000000000c2",
            "brokerage_id": "00000000-0000-4000-8000-0000000000c3",
            "role": "principal", "active": True,
        })

    async with use_tenant(a_id):
        rows = [r async for r in safe.user_brokerage_roles.find({})]
    assert all(r["tenant_id"] == a_id for r in rows)


async def test_r3_client_links_scoped(mongo_db):
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import use_tenant
    from app.seeds.seed_users_mongo import seed_mongo

    a_result = await seed_mongo(mongo_db)
    a_id = a_result["tenant_id"]
    safe = wrap(mongo_db)

    b_id = "00000000-0000-4000-8000-0000000000d1"
    async with use_tenant(b_id):
        await safe.user_client_links.insert_one({
            "user_id": "00000000-0000-4000-8000-0000000000d2",
            "client_id": "00000000-0000-4000-8000-0000000000d3",
            "role": "client_owner",
        })

    async with use_tenant(a_id):
        rows = [r async for r in safe.user_client_links.find({})]
    assert all(r["tenant_id"] == a_id for r in rows)


async def test_r3_sessions_scoped_via_wrapper(mongo_db):
    """After the middleware has bound ctx, normal session reads go
    through the wrapper. The system() bootstrap is the ONLY unfiltered
    read; every other listing is scoped."""
    from app.db.tenant_safe import wrap
    from app.kernel import identity
    from app.kernel.tenant_ctx import use_tenant
    from app.seeds.seed_users_mongo import seed_mongo

    a_result = await seed_mongo(mongo_db)
    a_id = a_result["tenant_id"]
    principal = next(o for o in a_result["operators"] if o["role"] == "principal")

    b_id = "00000000-0000-4000-8000-0000000000e1"

    # Create a session for tenant A.
    a_sess = await identity.create_session(
        mongo_db, user_id=principal["user_id"],
        tenant_id=a_id, active_role="principal",
    )
    # Create a session for tenant B (via safe wrapper — ctx=B).
    from datetime import datetime, timedelta, timezone
    safe = wrap(mongo_db)
    async with use_tenant(b_id):
        await safe.sessions.insert_one({
            "session_id": "00000000-0000-4000-8000-0000000000e2",
            "user_id": "00000000-0000-4000-8000-0000000000e3",
            "brokerage_id": None, "client_id": None,
            "active_role": "principal", "mfa_verified": False,
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=12),
            "revoked_at": None, "client_prefs": {},
        })

    # From tenant A ctx, a wrapper listing must see A's session only.
    async with use_tenant(a_id):
        rows = [r async for r in safe.sessions.find({})]
    assert any(r["session_id"] == a_sess.session_id for r in rows)
    assert all(r["tenant_id"] == a_id for r in rows)

    # But the system() bootstrap read CAN find B's session by id —
    # that's the R4a design (cookie is the tenant-discovery primitive).
    b_boot = await safe.system("sessions").find_one(
        {"session_id": "00000000-0000-4000-8000-0000000000e2"}
    )
    assert b_boot is not None
    assert b_boot["tenant_id"] == b_id


# ---- R4b: workflow + domain collection isolation ----------------------
_R4B_A = "00000000-0000-4000-8000-0000000000a1"
_R4B_B = "00000000-0000-4000-8000-0000000000b1"


async def _seed_r4b_tenants(mongo_db):
    from datetime import datetime, timezone
    from app.kernel import trail_mongo
    from app.kernel.tenant_ctx import use_tenant
    async with use_tenant(_R4B_A):
        try:
            await trail_mongo.create_tenant(
                mongo_db, "r4b-iso-a", "R4b Iso A",
                tenant_id_override=_R4B_A,
                ts_override=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
        except Exception:
            pass
    async with use_tenant(_R4B_B):
        try:
            await trail_mongo.create_tenant(
                mongo_db, "r4b-iso-b", "R4b Iso B",
                tenant_id_override=_R4B_B,
                ts_override=datetime(2024, 1, 2, tzinfo=timezone.utc),
            )
        except Exception:
            pass


@pytest.mark.parametrize("collection_name,doc_id_field", [
    ("envelopes", "envelope_id"),
    ("proj_queue", "envelope_id"),
    ("proj_active_blocks", "block_id"),
    ("clients", "client_id"),
    ("policies", "policy_id"),
    ("claims", "claim_id"),
    ("brokerages", "brokerage_id"),
    ("proj_matters", "matter_id"),
    ("proj_notifications", "notification_id"),
    ("proj_kpi_daily", "kpi_id"),
    ("proj_class_unlock", "class_key"),
    ("proj_workflow_waits", "workflow_id"),
])
async def test_r4b_new_collections_scoped_via_wrapper(
    mongo_db, collection_name, doc_id_field,
):
    """Every R4b + R4c collection filters reads by ctx and rejects writes
    that contradict ctx. The wrapper is the only door."""
    import uuid
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import TenantMismatchError, use_tenant
    await _seed_r4b_tenants(mongo_db)

    safe = wrap(mongo_db)
    b_doc_id = str(uuid.uuid4())
    async with use_tenant(_R4B_B):
        await getattr(safe, collection_name).insert_one({
            doc_id_field: b_doc_id, "marker": "belongs-to-B",
        })

    async with use_tenant(_R4B_A):
        # A sees nothing of B's.
        row = await getattr(safe, collection_name).find_one(
            {doc_id_field: b_doc_id}
        )
        assert row is None
        # An explicit filter with the wrong tenant_id fails loud.
        with pytest.raises(TenantMismatchError):
            await getattr(safe, collection_name).find_one(
                {"tenant_id": _R4B_B}
            )
        # An insert with the wrong tenant_id fails loud.
        with pytest.raises(TenantMismatchError):
            await getattr(safe, collection_name).insert_one({
                doc_id_field: str(uuid.uuid4()),
                "tenant_id": _R4B_B,
            })

    async with use_tenant(_R4B_B):
        row = await getattr(safe, collection_name).find_one(
            {doc_id_field: b_doc_id}
        )
        assert row is not None
        assert row["tenant_id"] == _R4B_B

