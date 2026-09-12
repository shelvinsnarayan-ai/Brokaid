"""Phase 4 — Cross-tenant isolation (RLS + 404-on-miss headline test)."""
from __future__ import annotations

import uuid

import pytest

from app.kernel.trail import append, create_tenant

pytestmark = pytest.mark.asyncio


async def test_matters_endpoint_returns_only_own_tenant_via_header(app_client):
    app, client = app_client
    pool = app.state.pool

    a, _ = await create_tenant(pool, "iso-a", "Iso A")
    b, _ = await create_tenant(pool, "iso-b", "Iso B")

    matter_a_id = str(uuid.uuid4())
    matter_b_id = str(uuid.uuid4())
    await append(
        pool, a.tenant_id, "MatterCreated",
        actor={"kind": "system", "id": "s", "name": "s"},
        payload={"matter_id": matter_a_id, "client_id": str(uuid.uuid4()),
                 "status": "new", "subject": "A"},
        idempotency_key="iso-a-mc",
    )
    await append(
        pool, b.tenant_id, "MatterCreated",
        actor={"kind": "system", "id": "s", "name": "s"},
        payload={"matter_id": matter_b_id, "client_id": str(uuid.uuid4()),
                 "status": "new", "subject": "B"},
        idempotency_key="iso-b-mc",
    )

    # /api/o/matters is header-driven (X-Tenant-Slug). Tenant A sees A only.
    r = await client.get(
        "/api/o/matters", headers={"X-Tenant-Slug": "iso-a"}
    )
    assert r.status_code == 200
    ids = {m["matter_id"] for m in r.json()["matters"]}
    assert ids == {matter_a_id}

    # A tenant slug that doesn't exist → 404 (hides existence).
    r = await client.get(
        "/api/o/matters", headers={"X-Tenant-Slug": "not-a-real-tenant"}
    )
    assert r.status_code == 404


async def test_raw_sql_rls_blocks_cross_tenant_reads(app_client):
    app, _ = app_client
    pool = app.state.pool
    a, _ = await create_tenant(pool, "iso-sql-a", "A")
    b, _ = await create_tenant(pool, "iso-sql-b", "B")

    ev_b = await append(
        pool, b.tenant_id, "NoteRecorded",
        actor={"kind": "system", "id": "s", "name": "s"},
        payload={"secret": "b-only"},
        idempotency_key="iso-sql-b-secret",
    )

    async with pool.acquire() as c:
        async with c.transaction():
            # SET GUC to tenant A, try to read tenant B rows.
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", a.tenant_id
            )
            rows = await c.fetch(
                "SELECT event_id FROM events WHERE tenant_id = $1", b.tenant_id
            )
    assert rows == [], "RLS leaked cross-tenant rows"
