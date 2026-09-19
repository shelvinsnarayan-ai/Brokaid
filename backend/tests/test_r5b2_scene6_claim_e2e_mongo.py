"""Phase R5b-2b — Scene 6 (claims FNOL) end-to-end under
`BROKAID_LEDGER=mongo` + `BROKAID_IDENTITY=mongo` + `BROKAID_DOMAIN=mongo`.

Piggybacks the `mongo_e2e` fixture from `test_queue_e2e_mongo.py`
(full TestClient stack, routes → middleware → dispatcher → tenant-
safe wrapper → Mongo). Extends it with:
  - CI tenant A seeded in Postgres (so brAIn's PG-only telemetry
    writes have a valid tenants FK — brAIn dispatch conversion is
    R5c work; this is an accepted PG-only side-effect for now).
  - A client + brokerage + user_client_link in Mongo tenant A.
  - A client_owner session in Mongo tenant A.

Five acceptance tests (matching root's approved R5b-2b brief):
  1. FNOL POST /api/c/claims → 200, state=lodged, 5 always-emitted
     FNOL events land in Mongo with correct types + idempotency
     keys + `mocked=True` on the two insurer events. Claim row
     lands with `status='lodged'`; 2 proj_claim_status_updates rows
     land; EmailRenderedDevMode + InAppNotification events land;
     proj_notifications row lands.
  2. Operator status-update with a coverage-guard-tripping draft
     → verdict='coverage_guard_block', no release event emitted.
  3. Operator resolve → 200, ClaimResolved event lands, claim row
     `status='resolved'`, resolved status-update projection lands.
  4. Cross-tenant resolve negative: unresolved bogus claim → 404.
  5. RBAC negative: client_owner cannot call `/api/o/claims/.../
     resolve` (client kind can't take operator sessions).
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from app.auth.sessions import COOKIE_NAME

pytestmark = pytest.mark.asyncio

pytest_plugins = ["tests.test_queue_e2e_mongo"]


def _cookie(sid: str) -> dict[str, str]:
    return {COOKIE_NAME: sid}


@pytest_asyncio.fixture
async def scene6_ctx(mongo_e2e, pool):
    """Extends `mongo_e2e` with a client + brokerage + user_client_link
    in Mongo tenant A, plus a client_owner session, plus a matching
    tenants row in Postgres for the brAIn telemetry writes."""
    from app.db.tenant_safe import wrap
    from app.kernel import identity, trail
    from app.kernel.tenant_ctx import use_tenant

    tenant_a = mongo_e2e["tenant_a"]

    # Seed the CI tenant in Postgres so brAIn's PG-only
    # AgentInvocation writes have a valid tenants FK. This is a
    # one-off compat until R5c makes brAIn dispatch-aware.
    slug = f"ci-scene6-{uuid.uuid4().hex[:6]}"
    await trail.create_tenant(
        pool, slug=slug, display_name=f"CI Scene6 {slug}",
        tenant_id_override=tenant_a,
    )

    safe = wrap(mongo_e2e["mongo"])

    client_id = str(uuid.uuid4())
    client_user_id = str(uuid.uuid4())
    async with use_tenant(tenant_a):
        await safe.brokerages.insert_one({
            "brokerage_id": str(uuid.uuid4()),
            "display_name": "Fictional Broker Pty Ltd",
        })
        await safe.clients.insert_one({
            "client_id": client_id,
            "display_name": "Ripple Constructions Pty Ltd",
            "status": "active",
        })
        await safe.users.insert_one({
            "user_id": client_user_id,
            "email": "cli-scene6-a@ci.example",
            "display_name": "Ripple Owner", "kind": "client",
            "password_hash": None, "mfa_secret": None,
        })
        await safe.user_client_links.insert_one({
            "user_id": client_user_id,
            "client_id": client_id,
            "role": "client_owner",
        })
    client_sess = await identity.create_session(
        mongo_e2e["mongo"], user_id=client_user_id,
        tenant_id=tenant_a, client_id=client_id,
        active_role="client_owner", mfa_verified=True,
    )

    yield {
        **mongo_e2e,
        "client_id": client_id,
        "client_user_id": client_user_id,
        "client_session_id": client_sess.session_id,
    }


# ---- 1. FNOL end-to-end on Mongo ------------------------------------
async def test_r5b2b_scene6_fnol_end_to_end_on_mongo(scene6_ctx):
    """POST /api/c/claims runs the whole scene 6. Assert every write
    landed in Mongo with the pre-extraction shape."""
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import use_tenant

    r = await scene6_ctx["client"].post(
        "/api/c/claims",
        json={
            "description": "Water damage in the warehouse from storm.",
            "incident_date": "2025-06-14",
        },
        cookies=_cookie(scene6_ctx["client_session_id"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "lodged"
    claim_id = body["claim_id"]
    envelope_id = body["envelope_id"]
    claim_ref = body["claim_reference"]
    assert claim_ref.startswith("CLM-")

    safe = wrap(scene6_ctx["mongo"])
    async with use_tenant(scene6_ctx["tenant_a"]):
        # The 5 always-emitted FNOL events (QueueCardEnqueued is
        # conditional on urgency=critical / coverage-check — not
        # asserted here to keep the test triage-stub-independent).
        expected = [
            ("ClientRequestReceived", f"claim:req:{envelope_id}", False),
            ("ClaimTriaged", f"claim:triaged:{envelope_id}", False),
            ("ClaimCreated", f"claim:created:{claim_id}", False),
            ("InsurerLodgementRequested", f"lodgement:req:{claim_id}", True),
            ("InsurerLodgementAck", f"lodgement:ack:{claim_id}", True),
        ]
        for typ, idem, mocked in expected:
            row = await safe.events.find_one({
                "type": typ,
                "payload.envelope_id": envelope_id,
            })
            assert row is not None, f"missing {typ}"
            assert row["idempotency_key"] == idem, (
                f"{typ}: idempotency_key mismatch"
            )
            assert row["mocked"] is mocked, f"{typ}: mocked={row['mocked']}"

        # Claim row landed and is lodged.
        claim = await safe.claims.find_one({"claim_id": claim_id})
        assert claim is not None, "claim row missing"
        assert claim["status"] == "lodged"
        assert claim["claim_reference"] == claim_ref

        # 2 status-update projection rows: triaged + lodged.
        n_status = 0
        seen_kinds = set()
        async for r_su in safe.proj_claim_status_updates.find(
            {"claim_id": claim_id}
        ):
            n_status += 1
            seen_kinds.add(r_su["kind"])
        assert n_status == 2, f"expected 2 status-update rows, got {n_status}"
        assert seen_kinds == {"triaged", "lodged"}, seen_kinds

        # Notification: EmailRenderedDevMode + InAppNotification + proj row.
        ered = await safe.events.find_one({"type": "EmailRenderedDevMode"})
        inapp = await safe.events.find_one({"type": "InAppNotification"})
        assert ered is not None
        assert inapp is not None
        n_proj = 0
        async for _ in safe.proj_notifications.find({}):
            n_proj += 1
        assert n_proj >= 1


# ---- 2. Status-update coverage-guard-block ---------------------------
async def test_r5b2b_scene6_status_update_coverage_guard_block(scene6_ctx):
    """A draft that trips the deterministic coverage guard returns
    verdict='coverage_guard_block' and emits NO release event."""
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import use_tenant

    # First FNOL to create a claim.
    r_fnol = await scene6_ctx["client"].post(
        "/api/c/claims",
        json={"description": "Water damage in warehouse from storm."},
        cookies=_cookie(scene6_ctx["client_session_id"]),
    )
    assert r_fnol.status_code == 200
    claim_id = r_fnol.json()["claim_id"]

    # Count release events before.
    safe = wrap(scene6_ctx["mongo"])
    async with use_tenant(scene6_ctx["tenant_a"]):
        n_before = 0
        async for _ in safe.events.find({
            "type": {"$in": [
                "ClientMessageReleased",
                "ClientMessageRewriteRequired",
                "ClientMessageHITL",
            ]},
        }):
            n_before += 1

    r = await scene6_ctx["client"].post(
        f"/api/o/claims/{claim_id}/status-update",
        json={"draft": "You're covered for this. No question, we'll pay it."},
        cookies=_cookie(scene6_ctx["session_op_a"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["released"] is False
    assert body["verdict"] == "coverage_guard_block"
    assert body["guard_visible"] is True
    assert isinstance(body["blocked_spans"], list) and body["blocked_spans"]

    # No release event landed.
    async with use_tenant(scene6_ctx["tenant_a"]):
        n_after = 0
        async for _ in safe.events.find({
            "type": {"$in": [
                "ClientMessageReleased",
                "ClientMessageRewriteRequired",
                "ClientMessageHITL",
            ]},
        }):
            n_after += 1
    assert n_after == n_before, (
        f"coverage_guard_block leaked a release event: "
        f"before={n_before} after={n_after}"
    )


# ---- 3. Resolve writes ClaimResolved on Mongo ------------------------
async def test_r5b2b_scene6_resolve_writes_claim_resolved_on_mongo(scene6_ctx):
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import use_tenant

    r_fnol = await scene6_ctx["client"].post(
        "/api/c/claims",
        json={"description": "Cracked window from vandalism last night."},
        cookies=_cookie(scene6_ctx["client_session_id"]),
    )
    assert r_fnol.status_code == 200
    claim_id = r_fnol.json()["claim_id"]
    claim_ref = r_fnol.json()["claim_reference"]

    r = await scene6_ctx["client"].post(
        f"/api/o/claims/{claim_id}/resolve",
        cookies=_cookie(scene6_ctx["session_op_a"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "resolved"
    assert body["event_id"]

    safe = wrap(scene6_ctx["mongo"])
    async with use_tenant(scene6_ctx["tenant_a"]):
        ev = await safe.events.find_one({"event_id": body["event_id"]})
        assert ev is not None
        assert ev["type"] == "ClaimResolved"
        assert ev["payload"]["claim_id"] == claim_id
        assert ev["payload"]["claim_reference"] == claim_ref
        assert ev["payload"]["status"] == "resolved"

        claim = await safe.claims.find_one({"claim_id": claim_id})
        assert claim is not None
        assert claim["status"] == "resolved"
        assert claim.get("resolved_at") is not None

        # A resolved status-update projection row also landed.
        resolved_row = await safe.proj_claim_status_updates.find_one(
            {"claim_id": claim_id, "kind": "resolved"},
        )
        assert resolved_row is not None


# ---- 4. Cross-tenant resolve negative --------------------------------
async def test_r5b2b_scene6_resolve_nonexistent_claim_404(scene6_ctx):
    """A random UUID for a claim that doesn't exist in tenant A returns
    404 (proves the operator's tenant-scoped read guards resolve)."""
    r = await scene6_ctx["client"].post(
        f"/api/o/claims/{uuid.uuid4()}/resolve",
        cookies=_cookie(scene6_ctx["session_op_a"]),
    )
    assert r.status_code == 404, r.text


# ---- 5. RBAC negative: client cannot resolve -------------------------
async def test_r5b2b_scene6_client_cannot_resolve_claim(scene6_ctx):
    """The client_owner session must be rejected by the operator
    session dependency on /api/o/claims/{id}/resolve (403)."""
    r_fnol = await scene6_ctx["client"].post(
        "/api/c/claims",
        json={"description": "Broken skylight from hail."},
        cookies=_cookie(scene6_ctx["client_session_id"]),
    )
    assert r_fnol.status_code == 200
    claim_id = r_fnol.json()["claim_id"]

    r = await scene6_ctx["client"].post(
        f"/api/o/claims/{claim_id}/resolve",
        cookies=_cookie(scene6_ctx["client_session_id"]),
    )
    assert r.status_code == 403, r.text
