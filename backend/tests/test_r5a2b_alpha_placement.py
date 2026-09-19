"""Phase R5a-2b-α — placement extraction regression + Scene 2 E2E.

Regression pins (extract-first discipline)
------------------------------------------
The four placement event emitters + two readers were extracted
verbatim from `routes/o_placement.py` into `kernel/placement.py`.
Since the extraction is a byte-verbatim move — same event types,
same payload dict literals, same idempotency_key patterns, same
actor shapes, same JCS canonicalisation → the R2 hash chain
guarantees byte-identical event_hash values across the extraction.

These tests pin the four event shapes by calling the kernel
helpers directly against a fresh Postgres tenant and asserting:
  - the event lands in `events` with the expected `type`
  - the payload keys are exactly what the pre-extraction path
    would have produced
  - the idempotency_key matches the pre-extraction pattern
  - the actor shape matches the pre-extraction pattern

Scene 2 E2E — byte-identical ledger parity
-------------------------------------------
Same input placement event → same event_hash on Postgres AND Mongo.
This IS the R2 guarantee under Mongo; the assertion is on hex equality.
Uses `ts_override` + `event_id_override` on the underlying `trail.append`
and `trail_mongo.append_event` primitives (allow-listed for tests only —
see `ci/ts_override_scan.js`).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.asyncio


# ---- Helpers ----------------------------------------------------------
async def _pg_tenant(pool):
    from app.kernel import trail
    slug = f"place-{uuid.uuid4().hex[:6]}"
    t, _ = await trail.create_tenant(pool, slug=slug, display_name=f"Placement {slug}")
    return t.tenant_id


async def _fetch_event(pool, tid: str, event_id: str) -> dict:
    """Fetch an event under the RLS-bound tenant context so
    `current_setting('app.tenant_id', true)::uuid` can evaluate."""
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tid,
            )
            row = await c.fetchrow(
                "SELECT type, actor, payload, idempotency_key, mocked "
                "FROM events "
                "WHERE tenant_id=$1::uuid AND event_id=$2::uuid",
                tid, event_id,
            )
    assert row is not None, f"event {event_id} not found under tenant {tid}"
    actor = row["actor"] if isinstance(row["actor"], dict) else json.loads(row["actor"])
    payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
    return {
        "type": row["type"], "actor": actor, "payload": payload,
        "idempotency_key": row["idempotency_key"], "mocked": row["mocked"],
    }


async def _shared_tenant(pool, mongo_db):
    """Create tenant on BOTH stores with same tenant_id + same
    TenantGenesis (matched ts_override + event_id_override) → same
    prev_hash → same event_hash for the next append (given equal
    payload + ts_override + event_id_override)."""
    from app.kernel import trail, trail_mongo
    from app.kernel.tenant_ctx import use_tenant
    slug = f"place-{uuid.uuid4().hex[:6]}"
    frozen_tid = str(uuid.uuid4())
    frozen_genesis_eid = str(uuid.uuid4())
    frozen_genesis_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    # PG genesis with the same overrides Mongo will use.
    await trail.create_tenant(
        pool, slug=slug, display_name=f"Placement {slug}",
        tenant_id_override=frozen_tid,
        ts_override=frozen_genesis_ts,
        event_id_override=frozen_genesis_eid,
    )
    async with use_tenant(frozen_tid):
        await trail_mongo.create_tenant(
            mongo_db, slug, f"Placement {slug}",
            tenant_id_override=frozen_tid,
            ts_override=frozen_genesis_ts,
            event_id_override=frozen_genesis_eid,
        )
    return frozen_tid


# ---- Regression pin 1: SubmissionDrafted ------------------------------
async def test_r5a2ba_pin_submission_drafted(pool):
    from app.kernel import placement
    tid = await _pg_tenant(pool)
    envelope_id = str(uuid.uuid4())
    policy_id = str(uuid.uuid4())
    ev = await placement.emit_submission_drafted(
        pool, tenant_id=tid, envelope_id=envelope_id,
        submission_summary="pin",
        structured_submission={"a": 1},
        recommended_wording_ref="WR-1",
        policy_id=policy_id,
    )
    row = await _fetch_event(pool, tid, ev["event_id"])
    assert row["type"] == "SubmissionDrafted"
    assert row["actor"] == {"kind": "system", "id": "placement", "name": "placement"}
    assert set(row["payload"].keys()) == {
        "envelope_id", "submission_summary", "structured_submission",
        "recommended_wording_ref", "policy_id", "mocked",
    }
    assert row["payload"]["mocked"] is False
    assert row["idempotency_key"] == f"placement:submission:{envelope_id}"


# ---- Regression pin 2: CarrierQuoted ---------------------------------
async def test_r5a2ba_pin_carrier_quoted(pool):
    from app.kernel import placement
    tid = await _pg_tenant(pool)
    envelope_id = str(uuid.uuid4())
    quote = {
        "quote_id": "Q-1", "carrier_name": "MockCo",
        "premium_change_amount": "100.00",
        "premium_new_annual": "1200.00", "premium_currency": "AUD",
        "terms": {"x": 1}, "conditions": ["y"],
        "quoted_at": "2025-01-01T00:00:00+00:00",
    }
    ev = await placement.emit_carrier_quoted(
        pool, tenant_id=tid, envelope_id=envelope_id,
        policy_id="P-1", quote=quote,
    )
    row = await _fetch_event(pool, tid, ev["event_id"])
    assert row["type"] == "CarrierQuoted"
    assert row["mocked"] is True
    assert row["actor"] == {"kind": "system", "id": "carrier_channel", "name": "carrier"}
    assert row["payload"]["mocked"] is True
    assert row["payload"]["quote_id"] == "Q-1"
    assert row["idempotency_key"] == f"carrier:quote:{envelope_id}"


# ---- Regression pin 3: EndorsementQuoteReady -------------------------
async def test_r5a2ba_pin_endorsement_quote_ready(pool):
    from app.kernel import placement
    tid = await _pg_tenant(pool)
    envelope_id = str(uuid.uuid4())
    ev = await placement.emit_endorsement_quote_ready(
        pool, tenant_id=tid, envelope_id=envelope_id,
        policy_id="P-1", quote_id="Q-1",
        intake_envelope_id="INT-1",
    )
    row = await _fetch_event(pool, tid, ev["event_id"])
    assert row["type"] == "EndorsementQuoteReady"
    assert row["actor"] == {"kind": "system", "id": "orchestrator", "name": "orchestrator"}
    assert set(row["payload"].keys()) == {
        "envelope_id", "policy_id", "quote_id", "intake_envelope_id",
    }
    assert row["idempotency_key"] == f"quote:ready:{envelope_id}"


# ---- Regression pin 4: PlacementRerouted -----------------------------
async def test_r5a2ba_pin_placement_rerouted(pool):
    from app.kernel import placement
    tid = await _pg_tenant(pool)
    envelope_id = str(uuid.uuid4())
    ev = await placement.emit_placement_rerouted(
        pool, tenant_id=tid, envelope_id=envelope_id,
        reroute_class="coverage_position",
        intake_envelope_id="INT-1", policy_id="P-1",
        notes="plAIce refused: coverage question",
    )
    row = await _fetch_event(pool, tid, ev["event_id"])
    assert row["type"] == "PlacementRerouted"
    assert row["actor"] == {"kind": "system", "id": "placement", "name": "placement"}
    assert set(row["payload"].keys()) == {
        "envelope_id", "reroute_class", "intake_envelope_id",
        "policy_id", "notes",
    }
    assert row["idempotency_key"] == f"placement:reroute:{envelope_id}"


# ---- Scene 2 E2E: byte-identical ledger parity across backends -------
async def test_r5a2ba_scene2_placement_ledger_byte_identical(pool, mongo_db):
    """Emit the SAME EndorsementQuoteReady event on both backends via
    the underlying `trail.append` (PG) and `trail_mongo.append_event`
    (Mongo) primitives with matched `ts_override` + `event_id_override`;
    assert byte-identical event_hash (R2 guarantee under Mongo).

    Using the primitives directly (rather than through
    `placement.emit_*`) keeps the `ts_override=` scanner allow-list
    tight — the kernel emit helpers stay override-free per root's
    tightening on step 2.
    """
    from app.kernel import trail, trail_mongo
    from app.kernel.tenant_ctx import use_tenant
    from app.kernel.trail_dispatch import tip_hash

    tid = await _shared_tenant(pool, mongo_db)
    envelope_id = str(uuid.uuid4())
    policy_id = str(uuid.uuid4())
    intake_env_id = str(uuid.uuid4())

    # Verbatim copy of the placement kernel's EndorsementQuoteReady
    # shape — this test pins parity on identical inputs.
    actor = {"kind": "system", "id": "orchestrator", "name": "orchestrator"}
    payload = {
        "envelope_id": envelope_id,
        "policy_id": policy_id,
        "quote_id": "Q-parity",
        "intake_envelope_id": intake_env_id,
    }
    idem = f"quote:ready:{envelope_id}"

    frozen_ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    frozen_eid = str(uuid.uuid4())

    # Emit on Postgres with frozen ts + event_id.
    pg_ev = await trail.append(
        pool, tid, "EndorsementQuoteReady",
        actor=actor, payload=payload, idempotency_key=idem,
        ts_override=frozen_ts, event_id_override=frozen_eid,
    )

    # Emit the SAME event on Mongo with same frozen ts + event_id.
    async with use_tenant(tid):
        mo_ev = await trail_mongo.append_event(
            mongo_db, tid, "EndorsementQuoteReady",
            actor=actor, payload=payload, idempotency_key=idem,
            ts_override=frozen_ts, event_id_override=frozen_eid,
        )

    # R2 byte-identical parity across backends.
    assert pg_ev.event_hash == mo_ev.event_hash, (
        f"ledger hash drift! pg={pg_ev.event_hash} mo={mo_ev.event_hash}"
    )

    # chain_tip on both stores now equals this event_hash.
    pg_tip = await tip_hash(pool, tid)
    mo_tip = await tip_hash(mongo_db, tid)
    assert pg_tip == mo_tip == pg_ev.event_hash


# ---- Scene 2 RBAC-negative: client cannot POST placement -------------
async def test_r5a2ba_scene2_client_cannot_place(pool):
    """Client-role session hitting /api/o/matters/{id}/place → 403.
    Verifies the route-level auth check still holds after extraction."""
    from httpx import ASGITransport, AsyncClient
    from app.main import create_app
    from app.auth.sessions import COOKIE_NAME, create as create_session
    from app.kernel import trail

    slug = f"rbac-{uuid.uuid4().hex[:6]}"
    t, _ = await trail.create_tenant(pool, slug=slug, display_name="RBAC")
    tid = t.tenant_id
    # Seed a client user under this tenant. `clients` schema (see
    # migrations/006_policies_and_clients.sql) is:
    #   client_id, tenant_id, display_name, industry, domicile,
    #   jurisdiction_code — no `status` column.
    client_user = str(uuid.uuid4())
    client_id = str(uuid.uuid4())
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tid,
            )
            await c.execute(
                "INSERT INTO users (user_id, email, display_name, kind) "
                "VALUES ($1::uuid, $2, $3, 'client')",
                client_user, f"cli-{slug}@ci.example", "cli",
            )
            await c.execute(
                "INSERT INTO clients "
                "  (client_id, tenant_id, display_name, industry, "
                "   domicile, jurisdiction_code) "
                "VALUES ($1::uuid, $2::uuid, 'RBAC Client', "
                "        'construction', 'AU', 'AU')",
                client_id, tid,
            )
            await c.execute(
                "INSERT INTO user_client_links (user_id, tenant_id, client_id, role) "
                "VALUES ($1::uuid, $2::uuid, $3::uuid, 'client_owner')",
                client_user, tid, client_id,
            )
    session = await create_session(
        pool, user_id=client_user, tenant_id=tid,
        client_id=client_id, active_role="client_owner", mfa_verified=True,
    )
    app = create_app(pool_override=pool, seed_dev_users=False)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as ac:
            r = await ac.post(
                f"/api/o/matters/{uuid.uuid4()}/place",
                json={"intake_envelope_id": str(uuid.uuid4()),
                      "policy_id": str(uuid.uuid4())},
                cookies={COOKIE_NAME: session.session_id},
            )
    assert r.status_code == 403, r.text
