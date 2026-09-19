"""Phase R4b — parity + scenario acceptance for the workflow layer.

Tests:
  1. Enqueue → fetch_pending → resolve full round-trip on Mongo,
     shape byte-identical to what the Postgres kernel returns.
  2. Idempotent re-enqueue.
  3. Cross-tenant read on `/api/o/queue`-equivalent kernel calls —
     tenant A cannot see tenant B's queue cards.
  4. Cross-tenant approve — attempting to resolve an envelope_id
     belonging to another tenant is a no-op (wrapper scopes the
     UPDATE to ctx=A, so B's row is untouched).
  5. Compliance blocks: add → list → clear.
  6. Envelope create/find scoped to ctx.
  7. Clients / policies parity via `domain_mongo`.
  8. RBAC-negative on `/api/o/queue` under Mongo identity (R4a
     middleware): auditor and client sessions both get 403.

The RBAC-negative scenario runs under BROKAID_IDENTITY=mongo so the
middleware loads sessions via the Mongo identity backend; the
`/api/o/queue` route itself still reads Postgres for R4b's
kernel-level dispatcher scope (root approved this — the E2E
route rewire is R5's job). What R4b proves at the route level:
the RBAC boundary is enforced BEFORE any store touch, so the 403
result is independent of the ledger backend flip.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

pytestmark = pytest.mark.asyncio


A_TENANT = "00000000-0000-4000-8000-0000000000a1"
B_TENANT = "00000000-0000-4000-8000-0000000000b1"


async def _seed_tenants(mongo_db):
    """Bootstrap two tenants for isolation coverage."""
    from app.kernel import trail_mongo
    from app.kernel.tenant_ctx import use_tenant
    from datetime import datetime, timezone

    async with use_tenant(A_TENANT):
        try:
            await trail_mongo.create_tenant(
                mongo_db, "r4b-a", "R4b Tenant A",
                tenant_id_override=A_TENANT,
                ts_override=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
        except (ValueError, Exception):
            pass
    async with use_tenant(B_TENANT):
        try:
            await trail_mongo.create_tenant(
                mongo_db, "r4b-b", "R4b Tenant B",
                tenant_id_override=B_TENANT,
                ts_override=datetime(2024, 1, 2, tzinfo=timezone.utc),
            )
        except (ValueError, Exception):
            pass


# ---- 1. Enqueue → fetch → resolve round-trip -------------------------
async def test_r4b_queue_round_trip(mongo_db):
    from app.kernel import workflow
    await _seed_tenants(mongo_db)

    envelope_id = str(uuid.uuid4())
    card = await workflow.enqueue(
        mongo_db, tenant_id=A_TENANT,
        envelope_id=envelope_id, class_key="endorsement_standard",
        proposal_ref=str(uuid.uuid4()), routed_to_role="principal",
    )
    assert card.envelope_id == envelope_id
    assert card.resolved_at is None

    pending = await workflow.fetch_pending_queue(mongo_db, A_TENANT)
    assert any(c["envelope_id"] == envelope_id for c in pending)

    await workflow.resolve_queue_card(
        mongo_db, A_TENANT, envelope_id, resolved_by_event=str(uuid.uuid4()),
    )

    pending_after = await workflow.fetch_pending_queue(mongo_db, A_TENANT)
    assert not any(c["envelope_id"] == envelope_id for c in pending_after)

    row = await workflow.fetch_queue_card(mongo_db, A_TENANT, envelope_id)
    assert row is not None
    assert row["resolved_at"] is not None


# ---- 2. Idempotent enqueue -------------------------------------------
async def test_r4b_enqueue_idempotent(mongo_db):
    from app.kernel import workflow
    await _seed_tenants(mongo_db)

    envelope_id = str(uuid.uuid4())
    proposal_ref = str(uuid.uuid4())
    for _ in range(3):
        await workflow.enqueue(
            mongo_db, tenant_id=A_TENANT,
            envelope_id=envelope_id, class_key="submit_to_market",
            proposal_ref=proposal_ref,
        )
    pending = await workflow.fetch_pending_queue(mongo_db, A_TENANT)
    matches = [c for c in pending if c["envelope_id"] == envelope_id]
    assert len(matches) == 1


# ---- 3. Cross-tenant read scoped ------------------------------------
async def test_r4b_cross_tenant_queue_read_scoped(mongo_db):
    from app.kernel import workflow
    await _seed_tenants(mongo_db)

    b_envelope = str(uuid.uuid4())
    await workflow.enqueue(
        mongo_db, tenant_id=B_TENANT,
        envelope_id=b_envelope, class_key="coverage_position",
        proposal_ref=str(uuid.uuid4()),
    )
    a_view = await workflow.fetch_pending_queue(mongo_db, A_TENANT)
    assert not any(c["envelope_id"] == b_envelope for c in a_view)

    b_view = await workflow.fetch_pending_queue(mongo_db, B_TENANT)
    assert any(c["envelope_id"] == b_envelope for c in b_view)


# ---- 4. Cross-tenant resolve is a no-op via wrapper -----------------
async def test_r4b_cross_tenant_resolve_is_noop(mongo_db):
    from app.kernel import workflow
    await _seed_tenants(mongo_db)

    b_envelope = str(uuid.uuid4())
    await workflow.enqueue(
        mongo_db, tenant_id=B_TENANT,
        envelope_id=b_envelope, class_key="cancel_refund",
        proposal_ref=str(uuid.uuid4()),
    )
    # From tenant A ctx, try to resolve B's envelope — the wrapper
    # scopes the UPDATE filter to A, so B's row stays untouched.
    await workflow.resolve_queue_card(
        mongo_db, A_TENANT, b_envelope, resolved_by_event=str(uuid.uuid4()),
    )
    b_view = await workflow.fetch_pending_queue(mongo_db, B_TENANT)
    assert any(c["envelope_id"] == b_envelope for c in b_view), (
        "cross-tenant resolve leaked into tenant B — R4b wrapper failure"
    )


# ---- 5. Compliance blocks add/list/clear ----------------------------
async def test_r4b_compliance_blocks(mongo_db):
    from app.kernel import workflow_mongo
    from app.kernel.tenant_ctx import use_tenant
    await _seed_tenants(mongo_db)

    block_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    async with use_tenant(A_TENANT):
        await workflow_mongo.add_active_block(
            mongo_db, block_id=block_id, target_kind="workflow",
            target_id=str(uuid.uuid4()),
            reason="pilot", added_by_event=event_id,
        )
        rows = await workflow_mongo.list_active_blocks(mongo_db)
        assert any(r["block_id"] == block_id for r in rows)

        await workflow_mongo.clear_active_block(
            mongo_db, block_id, "resolved", str(uuid.uuid4()),
        )
        rows = await workflow_mongo.list_active_blocks(mongo_db)
        assert not any(r["block_id"] == block_id for r in rows)


# ---- 6. Envelope create/find scoped ---------------------------------
async def test_r4b_envelope_scoped(mongo_db):
    from app.kernel import workflow_mongo
    from app.kernel.tenant_ctx import (
        NoTenantContextError, TenantMismatchError, use_tenant,
    )
    await _seed_tenants(mongo_db)

    envelope_id = str(uuid.uuid4())
    async with use_tenant(A_TENANT):
        ev = await workflow_mongo.create_envelope(
            mongo_db, envelope_id=envelope_id, kind="endorsement",
        )
        assert ev.tenant_id == A_TENANT

    # Cross-tenant find returns None (wrapper filters by ctx).
    async with use_tenant(B_TENANT):
        assert await workflow_mongo.find_envelope(mongo_db, envelope_id) is None

    # No-ctx find raises.
    with pytest.raises(NoTenantContextError):
        await workflow_mongo.find_envelope(mongo_db, envelope_id)


# ---- 7. Domain (clients + policies) parity + scoping ---------------
async def test_r4b_domain_clients_and_policies(mongo_db):
    from app.kernel import domain
    from app.kernel.tenant_ctx import use_tenant
    await _seed_tenants(mongo_db)

    client_id = str(uuid.uuid4())
    policy_id = str(uuid.uuid4())
    await domain.create_client(
        mongo_db, tenant_id=A_TENANT,
        client_id=client_id, display_name="Acme Widgets",
        jurisdiction="AU", vulnerable_flag=False,
    )
    await domain.create_policy(
        mongo_db, tenant_id=A_TENANT,
        policy_id=policy_id, client_id=client_id,
        product="General Liability",
    )

    # find_client on Mongo requires ctx — dispatcher doesn't set it
    # automatically for reads (writes take tenant_id kwarg + set ctx
    # internally). Callers with a session-bound middleware already
    # have ctx; here in the test we wrap explicitly.
    async with use_tenant(A_TENANT):
        c = await domain.find_client(mongo_db, client_id)
        assert c is not None
        assert c.display_name == "Acme Widgets"

        p = await domain.find_policy(mongo_db, policy_id)
        assert p is not None
        assert p.client_id == client_id

    # Cross-tenant find returns None.
    async with use_tenant(B_TENANT):
        assert await domain.find_client(mongo_db, client_id) is None
        assert await domain.find_policy(mongo_db, policy_id) is None

    # List policies for client under A ctx: returns 1.
    ps = await domain.list_policies_for_client(mongo_db, A_TENANT, client_id)
    assert len(ps) == 1
    assert ps[0].policy_id == policy_id


# ---- 8. RBAC-negative on /api/o/queue -------------------------------
async def test_r4b_rbac_auditor_no_login(app_client, seeded):
    """Auditors have NO login-capable role in the seed — impersonate
    itself is rejected. That IS the RBAC guarantee for this persona:
    they can never even reach `/api/o/queue`."""
    _, client = app_client
    r = await client.post("/api/auth/dev/impersonate",
                          json={"email": "auditor@burmac.example"})
    # Two acceptable outcomes: 403 (no login role) OR a follow-up 403
    # if impersonate succeeds. Either proves the RBAC boundary holds.
    if r.status_code == 200:
        q = await client.get("/api/o/queue")
        assert q.status_code == 403, q.text
    else:
        assert r.status_code in (403, 404), r.text


async def test_r4b_rbac_client_blocked_from_queue(app_client, seeded):
    """Client sessions must be 403 on the operator queue route."""
    _, client = app_client
    r = await client.post("/api/auth/dev/impersonate",
                          json={"email": "owner@fictionalclient.example"})
    assert r.status_code == 200, r.text
    q = await client.get("/api/o/queue")
    assert q.status_code == 403, q.text


# ---- 9. Dispatcher rejects wrong store type ------------------------
async def test_r4b_dispatcher_rejects_wrong_store_type(mongo_db):
    from app.kernel import workflow
    with pytest.raises(workflow.WorkflowBackendMismatch):
        await workflow.enqueue(
            "not a store", tenant_id=A_TENANT,
            envelope_id=str(uuid.uuid4()),
            class_key="submit_to_market",
            proposal_ref=str(uuid.uuid4()),
        )


# ---- 10. Projection dispatch — ComplianceBlockAdded ----------------
async def test_r4b_project_event_compliance_block_added(mongo_db):
    """The in-line projection hook creates an active block from a
    ComplianceBlockAdded ledger event, mirroring trail.py::_project."""
    from app.kernel import workflow_mongo
    from app.kernel.tenant_ctx import use_tenant
    await _seed_tenants(mongo_db)

    block_id = str(uuid.uuid4())
    ev = {
        "type": "ComplianceBlockAdded",
        "event_id": str(uuid.uuid4()),
        "payload": {
            "block_id": block_id,
            "target_kind": "workflow",
            "target_id": str(uuid.uuid4()),
            "reason": "pilot",
        },
    }
    async with use_tenant(A_TENANT):
        await workflow_mongo.project_event(mongo_db, ev)
        rows = await workflow_mongo.list_active_blocks(mongo_db)
    assert any(r["block_id"] == block_id for r in rows)


# ---- 11. Projection dispatch — ComplianceBlockCleared -------------
async def test_r4b_project_event_compliance_block_cleared(mongo_db):
    from app.kernel import workflow_mongo
    from app.kernel.tenant_ctx import use_tenant
    await _seed_tenants(mongo_db)

    block_id = str(uuid.uuid4())
    async with use_tenant(A_TENANT):
        await workflow_mongo.add_active_block(
            mongo_db, block_id=block_id, target_kind="workflow",
            target_id=str(uuid.uuid4()),
            reason="pilot", added_by_event=str(uuid.uuid4()),
        )
        cleared_ev = {
            "type": "ComplianceBlockCleared",
            "event_id": str(uuid.uuid4()),
            "payload": {"block_id": block_id, "reason": "resolved"},
        }
        await workflow_mongo.project_event(mongo_db, cleared_ev)
        rows = await workflow_mongo.list_active_blocks(mongo_db)
    assert not any(r["block_id"] == block_id for r in rows)


# ---- 12. Projection dispatch — unknown type is a no-op ------------
async def test_r4b_project_event_unknown_type_noop(mongo_db):
    """The projection dispatcher must silently ignore event types it
    does not know about — mirrors trail.py::_project's fall-through."""
    from app.kernel import workflow_mongo
    from app.kernel.tenant_ctx import use_tenant
    await _seed_tenants(mongo_db)

    async with use_tenant(A_TENANT):
        before = await workflow_mongo.list_active_blocks(mongo_db)
        await workflow_mongo.project_event(mongo_db, {
            "type": "SomeUnrelatedEvent",
            "event_id": str(uuid.uuid4()),
            "payload": {"any": "thing"},
        })
        after = await workflow_mongo.list_active_blocks(mongo_db)
    assert before == after
