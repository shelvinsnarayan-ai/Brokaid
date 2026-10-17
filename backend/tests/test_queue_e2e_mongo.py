"""Phase R4c — end-to-end acceptance for `/api/o/queue` under
`BROKAID_LEDGER=mongo` + `BROKAID_IDENTITY=mongo` + `BROKAID_DOMAIN=mongo`.

Uses the actual FastAPI TestClient stack (routes → middleware →
dispatcher → tenant-safe wrapper → Mongo). No mocked dispatchers.

Five binary-verifiable acceptance tests (per root's brief):
  1. Seed → GET /api/o/queue as operator → 200 + non-empty cards.
  2. POST /api/o/queue/{envelope_id}/approve → 200 + `Approval`
     event lands in Mongo `events`, chain tip advances.
  3. Auditor session on /api/o/queue → 403.
  4. Client session on /api/o/queue → 403.
  5. Cross-tenant approve (session=ci-alpha, envelope=ci-beta's) →
     404 or 403 (200 is a P0 leak).
"""
from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio

from app.auth.sessions import COOKIE_NAME

pytestmark = pytest.mark.asyncio


CI_A_ID = "b21fb26d-42a1-587a-b2b3-b1e6b9c48e6a"  # matches ci_deterministic._id("ci:tenant-a")
CI_B_ID = "27c86c60-3d1f-5d1a-836e-d31da7a930b3"  # matches ci_deterministic._id("ci:tenant-b")


@pytest_asyncio.fixture
async def mongo_e2e(monkeypatch, mongo_db, pool):
    """Boot the app under full-Mongo mode + seed the queue scenario."""
    monkeypatch.setenv("BROKAID_LEDGER", "mongo")
    monkeypatch.setenv("BROKAID_IDENTITY", "mongo")
    monkeypatch.setenv("BROKAID_DOMAIN", "mongo")
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    monkeypatch.setenv("ENVIRONMENT", "development")

    # 1. Seed CI tenants + events in Mongo (byte-identical to Postgres).
    from app.seeds.ci_deterministic import (
        CI_TENANT_A_ID, CI_TENANT_B_ID,
        seed_ci_deterministic_mongo,
    )
    await seed_ci_deterministic_mongo(mongo_db)

    # 2. Seed three users (operator/auditor/client) under tenant A and
    #    one operator under tenant B for the cross-tenant test.
    from app.db.tenant_safe import wrap
    from app.kernel import identity
    from app.kernel.tenant_ctx import use_tenant

    safe = wrap(mongo_db)
    op_user_a = str(uuid.uuid4())
    aud_user_a = str(uuid.uuid4())
    cli_user_a = str(uuid.uuid4())
    op_user_b = str(uuid.uuid4())
    async with use_tenant(CI_TENANT_A_ID):
        for uid, email, kind, role in [
            (op_user_a, "op-a@ci.example", "operator", "principal"),
            (aud_user_a, "aud-a@ci.example", "operator", "auditor"),
            (cli_user_a, "cli-a@ci.example", "client", "client_owner"),
        ]:
            await safe.users.insert_one({
                "user_id": uid, "email": email,
                "display_name": email.split("@")[0],
                "kind": kind, "password_hash": None, "mfa_secret": None,
            })
            if kind == "operator":
                await safe.user_brokerage_roles.insert_one({
                    "user_id": uid,
                    "brokerage_id": str(uuid.uuid4()),
                    "role": role, "active": True,
                })
            else:
                await safe.user_client_links.insert_one({
                    "user_id": uid,
                    "client_id": str(uuid.uuid4()),
                    "role": role,
                })
    async with use_tenant(CI_TENANT_B_ID):
        await safe.users.insert_one({
            "user_id": op_user_b, "email": "op-b@ci.example",
            "display_name": "op-b", "kind": "operator",
            "password_hash": None, "mfa_secret": None,
        })

    # 3. Enqueue two queue cards — one per tenant — via workflow dispatcher.
    from app.kernel import workflow
    env_a = str(uuid.uuid4())
    env_b = str(uuid.uuid4())
    prop_a = str(uuid.uuid4())
    prop_b = str(uuid.uuid4())
    await workflow.enqueue(
        mongo_db, tenant_id=CI_TENANT_A_ID,
        envelope_id=env_a, class_key="endorsement_standard",
        proposal_ref=prop_a, routed_to_role="principal",
    )
    await workflow.enqueue(
        mongo_db, tenant_id=CI_TENANT_B_ID,
        envelope_id=env_b, class_key="endorsement_standard",
        proposal_ref=prop_b, routed_to_role="principal",
    )

    # 4. Create sessions via identity dispatcher (Mongo backend).
    sess_op_a = await identity.create_session(
        mongo_db, user_id=op_user_a, tenant_id=CI_TENANT_A_ID,
        active_role="principal", mfa_verified=True,
    )
    sess_aud_a = await identity.create_session(
        mongo_db, user_id=aud_user_a, tenant_id=CI_TENANT_A_ID,
        active_role="auditor", mfa_verified=True,
    )
    sess_cli_a = await identity.create_session(
        mongo_db, user_id=cli_user_a, tenant_id=CI_TENANT_A_ID,
        active_role="client_owner", mfa_verified=True,
    )

    # 5. Boot app with mongo_db_override; use conftest pool for the
    #    Postgres side of `app.state.pool` (routes may still hit it
    #    on non-mongo paths, but our tests only touch /api/o/queue).
    from app.main import create_app
    app = create_app(
        pool_override=pool, mongo_db_override=mongo_db,
        seed_dev_users=False,
    )

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield {
                "app": app, "client": client, "mongo": mongo_db,
                "session_op_a": sess_op_a.session_id,
                "session_aud_a": sess_aud_a.session_id,
                "session_cli_a": sess_cli_a.session_id,
                "envelope_a": env_a,
                "envelope_b": env_b,
                "tenant_a": CI_TENANT_A_ID,
                "tenant_b": CI_TENANT_B_ID,
            }


def _cookie(session_id: str) -> dict[str, str]:
    return {COOKIE_NAME: session_id}


# ---- 1. Seed → GET /api/o/queue as operator → 200 + non-empty -------
async def test_r4c_e2e_get_queue_operator(mongo_e2e):
    r = await mongo_e2e["client"].get(
        "/api/o/queue", cookies=_cookie(mongo_e2e["session_op_a"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] >= 1
    assert any(c["envelope_id"] == mongo_e2e["envelope_a"] for c in body["cards"])
    # Cross-tenant: operator A must NOT see B's card.
    assert not any(c["envelope_id"] == mongo_e2e["envelope_b"] for c in body["cards"])


# ---- 2. Approve → 200 + Approval event + chain tip advances --------
async def test_r4c_e2e_approve_writes_approval_and_advances_tip(mongo_e2e):
    from app.kernel.trail_dispatch import tip_hash
    tip_before = await tip_hash(mongo_e2e["mongo"], mongo_e2e["tenant_a"])
    r = await mongo_e2e["client"].post(
        f"/api/o/queue/{mongo_e2e['envelope_a']}/approve",
        json={"reason": "e2e approve"},
        cookies=_cookie(mongo_e2e["session_op_a"]),
    )
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "resolved"
    approval_ev_id = r.json()["approval_event_id"]

    # Approval event landed in Mongo `events`.
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import use_tenant
    safe = wrap(mongo_e2e["mongo"])
    async with use_tenant(mongo_e2e["tenant_a"]):
        appr = await safe.events.find_one({"event_id": approval_ev_id})
    assert appr is not None
    assert appr["type"] == "Approval"
    assert appr["payload"]["envelope_id"] == mongo_e2e["envelope_a"]

    # Chain tip advanced (byte-identical hash preservation is the R2
    # guarantee; the tip advance IS the surface-level assertion).
    tip_after = await tip_hash(mongo_e2e["mongo"], mongo_e2e["tenant_a"])
    assert tip_after is not None
    assert tip_after != tip_before, "tip did not advance after Approval"
    assert tip_after == appr["event_hash"]


# ---- 3. Auditor → GET /api/o/queue → 403 ---------------------------
async def test_r4c_e2e_auditor_blocked_from_queue(mongo_e2e):
    r = await mongo_e2e["client"].get(
        "/api/o/queue", cookies=_cookie(mongo_e2e["session_aud_a"]),
    )
    assert r.status_code == 403, r.text


# ---- 4. Client → GET /api/o/queue → 403 ----------------------------
async def test_r4c_e2e_client_blocked_from_queue(mongo_e2e):
    r = await mongo_e2e["client"].get(
        "/api/o/queue", cookies=_cookie(mongo_e2e["session_cli_a"]),
    )
    assert r.status_code == 403, r.text


# ---- 5. Cross-tenant approve → 404/403 (NOT 200) -------------------
async def test_r4c_e2e_cross_tenant_approve_no_leak(mongo_e2e):
    # Session scoped to tenant A tries to approve tenant B's envelope.
    r = await mongo_e2e["client"].post(
        f"/api/o/queue/{mongo_e2e['envelope_b']}/approve",
        json={"reason": "attempted cross-tenant leak"},
        cookies=_cookie(mongo_e2e["session_op_a"]),
    )
    assert r.status_code in (403, 404), (
        f"cross-tenant approve returned {r.status_code} — 200 would be a P0 leak. "
        f"body={r.text}"
    )
    # And tenant B's card must still be pending.
    from app.kernel import workflow
    b_view = await workflow.fetch_pending_queue(
        mongo_e2e["mongo"], mongo_e2e["tenant_b"],
    )
    assert any(c["envelope_id"] == mongo_e2e["envelope_b"] for c in b_view), (
        "cross-tenant leak: tenant B's card was resolved"
    )


# ---- R5a-1 tests -----------------------------------------------------

# ---- Mongo-mode dev-impersonate → operator session usable end-to-end
async def test_r5a1_dev_impersonate_operator_under_mongo(mongo_e2e):
    """POST /api/auth/dev/impersonate under Mongo mode creates a
    session in Mongo, cookie works, and the operator can list queue."""
    from app.kernel.tenant_ctx import use_tenant
    from app.db.tenant_safe import wrap

    r = await mongo_e2e["client"].post(
        "/api/auth/dev/impersonate", json={"email": "op-a@ci.example"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant_id"] == mongo_e2e["tenant_a"]
    assert body["user_kind"] == "operator"

    # Session was written to Mongo — not Postgres.
    safe = wrap(mongo_e2e["mongo"])
    async with use_tenant(mongo_e2e["tenant_a"]):
        row = await safe.sessions.find_one({"session_id": body["session_id"]})
    assert row is not None
    assert row["mfa_verified"] is True

    # Cookie works end-to-end.
    r2 = await mongo_e2e["client"].get(
        "/api/o/queue", cookies=_cookie(body["session_id"]),
    )
    assert r2.status_code == 200
    assert r2.json()["count"] >= 1


# ---- Scene 1: intake → MatterCreated → proj_matters populates
async def test_r5a1_scene1_matter_created_projects(mongo_e2e):
    """Emit a MatterCreated event via the matters kernel dispatcher
    under Mongo mode; `proj_matters` gets populated by the in-line
    projection dispatch inside `trail_mongo.append_event`."""
    from app.db.tenant_safe import wrap
    from app.kernel import matters
    from app.kernel.tenant_ctx import use_tenant
    import uuid as _uuid

    matter_id = str(_uuid.uuid4())
    client_id = str(_uuid.uuid4())
    result = await matters.create_matter(
        mongo_e2e["mongo"],
        tenant_id=mongo_e2e["tenant_a"],
        matter_id=matter_id, client_id=client_id,
        status="draft", subject="SIRC Scene 1 intake demo",
        actor={"kind": "system", "id": "intake", "name": "intAIke"},
    )
    assert result["matter_id"] == matter_id
    assert result["event_id"]

    safe = wrap(mongo_e2e["mongo"])
    async with use_tenant(mongo_e2e["tenant_a"]):
        row = await safe.proj_matters.find_one({"matter_id": matter_id})
    assert row is not None
    assert row["client_id"] == client_id
    assert row["status"] == "draft"
    assert row["subject"] == "SIRC Scene 1 intake demo"

    # Cross-tenant: tenant B must not see this matter.
    async with use_tenant(mongo_e2e["tenant_b"]):
        b_view = await safe.proj_matters.find_one({"matter_id": matter_id})
    assert b_view is None


# ---- Scene 3: compliance block + clear via HTTP route
async def test_r5a1_scene3_compliance_block_and_clear(mongo_e2e):
    """Real TestClient stack — the operator with compliance role
    posts to /api/o/compliance/block, and the projection populates
    on Mongo. Then /api/o/compliance/clear/{block_id} removes it."""
    from app.db.tenant_safe import wrap
    from app.kernel import identity
    from app.kernel.tenant_ctx import use_tenant
    import uuid as _uuid

    # Seed a compliance-role user under tenant A.
    comp_user_id = str(_uuid.uuid4())
    async with use_tenant(mongo_e2e["tenant_a"]):
        safe = wrap(mongo_e2e["mongo"])
        await safe.users.insert_one({
            "user_id": comp_user_id, "email": "comp-a@ci.example",
            "display_name": "comp-a", "kind": "operator",
            "password_hash": None, "mfa_secret": None,
        })
        await safe.user_brokerage_roles.insert_one({
            "user_id": comp_user_id,
            "brokerage_id": str(_uuid.uuid4()),
            "role": "compliance", "active": True,
        })
    comp_sess = await identity.create_session(
        mongo_e2e["mongo"], user_id=comp_user_id,
        tenant_id=mongo_e2e["tenant_a"],
        active_role="compliance", mfa_verified=True,
    )

    # Block
    target_id = str(_uuid.uuid4())
    r = await mongo_e2e["client"].post(
        "/api/o/compliance/block",
        cookies=_cookie(comp_sess.session_id),
        json={"target_kind": "matter", "target_id": target_id,
              "reason": "SIRC scene 3 compliance halt"},
    )
    assert r.status_code == 200, r.text
    block_id = r.json()["block_id"]

    # Projection: proj_active_blocks has the block, scoped to tenant A.
    async with use_tenant(mongo_e2e["tenant_a"]):
        row = await safe.proj_active_blocks.find_one({"block_id": block_id})
    assert row is not None
    assert row["target_kind"] == "matter"
    assert row["target_id"] == target_id
    assert row.get("cleared_at") is None

    # Cross-tenant: tenant B does NOT see the block.
    async with use_tenant(mongo_e2e["tenant_b"]):
        b_view = await safe.proj_active_blocks.find_one({"block_id": block_id})
    assert b_view is None

    # Clear
    r2 = await mongo_e2e["client"].post(
        f"/api/o/compliance/clear/{block_id}",
        cookies=_cookie(comp_sess.session_id),
        json={"reason": "resolved by compliance officer"},
    )
    assert r2.status_code == 200, r2.text

    # Projection: block cleared_at set (workflow_mongo.clear_active_block
    # is invoked by the in-line projection dispatch).
    async with use_tenant(mongo_e2e["tenant_a"]):
        row2 = await safe.proj_active_blocks.find_one({"block_id": block_id})
    assert row2 is not None
    assert row2.get("cleared_at") is not None
