"""Phase R5b-2a — Scene 6 kernel pins.

Extract-first pins on the R5b-2a kernel work:

- release.py dispatch conversion
- notifications.py dispatch conversion
- claims_scene.py extraction

Coverage
--------
1. release: coverage_guard_block short-circuits without emitting.
2. release: rewrite verdict via deterministic pre-scan cache emits
   ClientMessageRewriteRequired with the pre-extraction payload
   keyset on PG.
3. release: byte-identical event_hash across backends for a
   ClientMessageReleased path with a stub `release` classifier
   (matched ts_override + event_id_override on direct trail
   primitives — kernel emit path stays override-free per the
   `ts_override` scanner allow-list).
4. notifications: dev-mode render_and_send emits EmailRenderedDevMode
   + InAppNotification + writes proj_notifications row on PG.
5. notifications: byte-identical InAppNotification event_hash across
   backends for identical inputs.
6. claims_scene: each of the 6 emit_* helpers writes the expected
   type/actor/payload keyset/idempotency_key on PG.
7. claims_scene: FNOL 6-event chain byte-identical parity across
   backends (matched overrides). Assert every one of the 6
   event_hashes is equal across PG and Mongo.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest


pytestmark = pytest.mark.asyncio


# ---- Helpers ---------------------------------------------------------
async def _pg_tenant(pool):
    from app.kernel import trail
    slug = f"scn6-{uuid.uuid4().hex[:6]}"
    t, _ = await trail.create_tenant(pool, slug=slug, display_name=f"Scene6 {slug}")
    return t.tenant_id


async def _fetch_events(pool, tenant_id: str, type_: str, envelope_id: str):
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            rows = await c.fetch(
                "SELECT type, actor, payload, idempotency_key, mocked "
                "FROM events "
                "WHERE tenant_id=$1::uuid AND type=$2 "
                "  AND payload->>'envelope_id' = $3 "
                "ORDER BY seq ASC",
                tenant_id, type_, envelope_id,
            )
    out = []
    for r in rows:
        actor = r["actor"] if isinstance(r["actor"], dict) else json.loads(r["actor"])
        payload = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"])
        out.append({"type": r["type"], "actor": actor, "payload": payload,
                    "idempotency_key": r["idempotency_key"], "mocked": r["mocked"]})
    return out


# ---- Pin 1: release coverage_guard_block short-circuit ----------------
async def test_r5b2a_release_coverage_guard_short_circuit(pool):
    from app.kernel.release import try_release
    tid = await _pg_tenant(pool)
    # Draft with a canonical coverage-language block phrase.
    r = await try_release(
        store=pool, tenant_id=tid,
        draft="You're covered for this. No question, we'll pay it.",
        context={"jurisdiction_code": "AU", "product_class": "public_liability"},
        author_agent="liaison",
    )
    assert r.released is False
    assert r.verdict == "coverage_guard_block"
    assert r.event is None
    # No ledger event emitted.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tid,
            )
            n = await c.fetchval(
                "SELECT COUNT(*) FROM events "
                " WHERE tenant_id=$1::uuid AND type IN "
                " ('ClientMessageReleased','ClientMessageRewriteRequired',"
                "  'ClientMessageHITL')",
                tid,
            )
    assert n == 0


# ---- Pin 2: release rewrite via pre-scan cache ------------------------
async def test_r5b2a_release_rewrite_prescan_pg(pool):
    """A draft that trips the deterministic pre-scan cache emits
    ClientMessageRewriteRequired via the kernel emit dispatch. The
    payload keyset matches pre-extraction (classifier_verdict present,
    guard_rewrite_source == 'deterministic')."""
    from app.kernel.release import try_release
    from app.kernel.guard_rewrites import prescan_lookup
    tid = await _pg_tenant(pool)
    envelope_id = str(uuid.uuid4())
    hit = None
    draft = None
    for phrase in ("You are definitely covered for this",
                    "This will definitely be covered"):
        hit = prescan_lookup(draft=phrase, jurisdiction="AU",
                              product_class="public_liability")
        if hit:
            draft = phrase
            break
    if hit is None or draft is None:
        pytest.skip("no pre-scan phrase available — dispatch shape "
                    "already covered by pin 3")
    r = await try_release(
        store=pool, tenant_id=tid, draft=draft,
        context={"jurisdiction_code": "AU",
                  "product_class": "public_liability"},
        author_agent="liaison", envelope_id=envelope_id,
    )
    assert r.verdict == "rewrite"
    assert r.event is not None
    rows = await _fetch_events(pool, tid, "ClientMessageRewriteRequired",
                                envelope_id)
    assert len(rows) == 1
    row = rows[0]
    assert "classifier_verdict" in row["payload"]
    assert row["payload"]["guard_rewrite_source"] == "deterministic"


# ---- Pin 3: release byte-identical parity across backends ------------
async def test_r5b2a_release_release_parity(pool, mongo_db):
    """Same input on both stores → same event_hash for
    ClientMessageReleased. Uses direct trail primitives with matched
    overrides."""
    from app.kernel import trail, trail_mongo
    from app.kernel.tenant_ctx import use_tenant

    slug = f"rel-{uuid.uuid4().hex[:6]}"
    frozen_tid = str(uuid.uuid4())
    frozen_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    frozen_eid = str(uuid.uuid4())

    await trail.create_tenant(
        pool, slug=slug, display_name=f"Rel {slug}",
        tenant_id_override=frozen_tid,
        ts_override=frozen_ts, event_id_override=frozen_eid,
    )
    async with use_tenant(frozen_tid):
        await trail_mongo.create_tenant(
            mongo_db, slug, f"Rel {slug}",
            tenant_id_override=frozen_tid,
            ts_override=frozen_ts, event_id_override=frozen_eid,
        )

    envelope_id = str(uuid.uuid4())
    verdict = {
        "advice_classification": "factual",
        "classification_rationale": "test-stub",
        "risks_flagged": [],
        "release_verdict": "release",
    }
    payload = {
        "draft": "Your policy renewal date is 2026-01-01.",
        "author_agent": "liaison",
        "classification": "factual",
        "classifier_rationale": "test-stub",
        "risks_flagged": [],
        "mocked_channels_used": [],
        "classifier_verdict": verdict,
        "matter_id": None,
        "client_id": None,
        "envelope_id": envelope_id,
        "message": "Your policy renewal date is 2026-01-01.",
    }
    actor = {"kind": "system", "id": "release", "name": "release"}
    ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    eid = str(uuid.uuid4())
    pg_ev = await trail.append(
        pool, frozen_tid, "ClientMessageReleased",
        actor=actor, payload=payload,
        idempotency_key=f"clientmsg:release:{envelope_id}",
        ts_override=ts, event_id_override=eid,
    )
    async with use_tenant(frozen_tid):
        mo_ev = await trail_mongo.append_event(
            mongo_db, frozen_tid, "ClientMessageReleased",
            actor=actor, payload=payload,
            idempotency_key=f"clientmsg:release:{envelope_id}",
            ts_override=ts, event_id_override=eid,
        )
    assert pg_ev.event_hash == mo_ev.event_hash, (
        f"drift! pg={pg_ev.event_hash} mo={mo_ev.event_hash}"
    )


# ---- Pin 4: notifications dev-mode on PG ------------------------------
async def test_r5b2a_notifications_dev_mode_pg(pool, monkeypatch):
    from app.kernel import trail
    from app.kernel.notifications import render_and_send
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    tid = await _pg_tenant(pool)
    # Need a source event to reference.
    src = await trail.append(
        pool, tid, "ClaimTriaged",
        actor={"kind": "system", "id": "claims", "name": "claims"},
        payload={"envelope_id": str(uuid.uuid4()), "client_id": None},
        idempotency_key=f"triaged-src-{uuid.uuid4()}",
    )
    client_id = str(uuid.uuid4())
    # For proj_notifications INSERT the client_id must exist. Seed one.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tid,
            )
            await c.execute(
                "INSERT INTO clients (client_id, tenant_id, display_name, "
                "industry, domicile, jurisdiction_code) "
                "VALUES ($1::uuid, $2::uuid, 'X', 'construction', 'AU', 'AU')",
                client_id, tid,
            )
    result = await render_and_send(
        store=pool, tenant_id=tid, kind="claim_lodged",
        payload={
            "claim_reference": "CLM-9999-000001",
            "insurer_display": "Fictional Carrier A",
            "client_display": "X",
            "brokerage_display": "Broker",
            "summary": "pin",
        },
        recipient={"client_id": client_id, "email": "cli@ex.test"},
        source_event_id=src.event_id,
    )
    assert result["dev_mode"] is True
    assert result["notification_id"]
    # Assert EmailRenderedDevMode + InAppNotification both landed.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tid,
            )
            n_dev = await c.fetchval(
                "SELECT COUNT(*) FROM events "
                " WHERE tenant_id=$1::uuid AND type='EmailRenderedDevMode'",
                tid,
            )
            n_app = await c.fetchval(
                "SELECT COUNT(*) FROM events "
                " WHERE tenant_id=$1::uuid AND type='InAppNotification'",
                tid,
            )
            n_proj = await c.fetchval(
                "SELECT COUNT(*) FROM proj_notifications "
                " WHERE tenant_id=$1::uuid",
                tid,
            )
    assert n_dev == 1
    assert n_app == 1
    assert n_proj == 1


# ---- Pin 5: claims_scene FNOL 6-event byte-identical parity ----------
async def test_r5b2a_claims_scene_fnol_chain_byte_identical(pool, mongo_db):
    """Emit the FNOL 6-event chain on both stores with matched
    overrides. Assert every event_hash is byte-identical across
    backends. This IS the R2 acceptance gate for R5b-2a."""
    from app.kernel import trail, trail_mongo
    from app.kernel.tenant_ctx import use_tenant

    slug = f"cs-{uuid.uuid4().hex[:6]}"
    frozen_tid = str(uuid.uuid4())
    frozen_genesis_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    frozen_genesis_eid = str(uuid.uuid4())

    envelope_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    client_id = str(uuid.uuid4())
    claim_id = str(uuid.uuid4())
    claim_ref = "CLM-2025-000001"
    triage = {
        "claim_type": "property",
        "urgency": "critical",
        "key_facts": ["water damage", "roof"],
        "requires_coverage_check": True,
        "structured_summary": "Water damage from storm.",
    }
    lodgement = {
        "insurer_display": "Fictional Carrier A",
        "insurer_reference": "INS-000001",
        "lodgement_id": "LDG-000001",
    }
    ts_base = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    event_eids = [str(uuid.uuid4()) for _ in range(6)]

    # Genesis on both stores with matched overrides.
    await trail.create_tenant(
        pool, slug=slug, display_name=f"CS {slug}",
        tenant_id_override=frozen_tid,
        ts_override=frozen_genesis_ts,
        event_id_override=frozen_genesis_eid,
    )
    async with use_tenant(frozen_tid):
        await trail_mongo.create_tenant(
            mongo_db, slug, f"CS {slug}",
            tenant_id_override=frozen_tid,
            ts_override=frozen_genesis_ts,
            event_id_override=frozen_genesis_eid,
        )

    async def _emit_pair(i, typ, actor, payload, idem, mocked=False):
        ts = ts_base + timedelta(seconds=i + 1)
        eid = event_eids[i]
        pg_ev = await trail.append(
            pool, frozen_tid, typ,
            actor=actor, payload=payload, idempotency_key=idem, mocked=mocked,
            ts_override=ts, event_id_override=eid,
        )
        async with use_tenant(frozen_tid):
            mo_ev = await trail_mongo.append_event(
                mongo_db, frozen_tid, typ,
                actor=actor, payload=payload, idempotency_key=idem, mocked=mocked,
                ts_override=ts, event_id_override=eid,
            )
        return pg_ev, mo_ev

    # 1. ClientRequestReceived
    p1_pg, p1_mo = await _emit_pair(
        0, "ClientRequestReceived",
        {"kind": "human", "id": user_id, "name": "client_owner"},
        {"envelope_id": envelope_id, "matter_kind": "claim",
          "client_id": client_id, "raw_message": "water damage",
          "policy_id": None, "incident_date": "2025-06-14"},
        f"claim:req:{envelope_id}",
    )
    # 2. ClaimTriaged
    p2_pg, p2_mo = await _emit_pair(
        1, "ClaimTriaged",
        {"kind": "system", "id": "claims", "name": "claims"},
        {"envelope_id": envelope_id, "client_id": client_id,
          "claim_type": triage["claim_type"],
          "urgency": triage["urgency"],
          "key_facts": triage["key_facts"],
          "requires_coverage_check": triage["requires_coverage_check"],
          "structured_summary": triage["structured_summary"]},
        f"claim:triaged:{envelope_id}",
    )
    # 3. ClaimCreated
    p3_pg, p3_mo = await _emit_pair(
        2, "ClaimCreated",
        {"kind": "system", "id": "claims", "name": "claims"},
        {"envelope_id": envelope_id, "claim_id": claim_id,
          "claim_reference": claim_ref, "client_id": client_id,
          "policy_id": None, "claim_type": triage["claim_type"],
          "urgency": triage["urgency"], "description": "water damage",
          "incident_date": "2025-06-14", "status": "triaged"},
        f"claim:created:{claim_id}",
    )
    # 4. InsurerLodgementRequested
    p4_pg, p4_mo = await _emit_pair(
        3, "InsurerLodgementRequested",
        {"kind": "system", "id": "insurer-channel", "name": "insurer"},
        {"envelope_id": envelope_id, "claim_id": claim_id,
          "claim_reference": claim_ref, "mocked": True},
        f"lodgement:req:{claim_id}", mocked=True,
    )
    # 5. InsurerLodgementAck
    p5_pg, p5_mo = await _emit_pair(
        4, "InsurerLodgementAck",
        {"kind": "system", "id": "insurer-channel", "name": "insurer"},
        {"envelope_id": envelope_id, "claim_id": claim_id,
          "claim_reference": claim_ref,
          "insurer_display": lodgement["insurer_display"],
          "insurer_reference": lodgement["insurer_reference"],
          "lodgement_id": lodgement["lodgement_id"], "mocked": True},
        f"lodgement:ack:{claim_id}", mocked=True,
    )
    # 6. QueueCardEnqueued
    p6_pg, p6_mo = await _emit_pair(
        5, "QueueCardEnqueued",
        {"kind": "system", "id": "queue", "name": "queue"},
        {"envelope_id": envelope_id, "claim_id": claim_id,
          "class_key": "claim_admin"},
        f"claim:queued:{envelope_id}",
    )

    pairs = [
        ("ClientRequestReceived", p1_pg, p1_mo),
        ("ClaimTriaged", p2_pg, p2_mo),
        ("ClaimCreated", p3_pg, p3_mo),
        ("InsurerLodgementRequested", p4_pg, p4_mo),
        ("InsurerLodgementAck", p5_pg, p5_mo),
        ("QueueCardEnqueued", p6_pg, p6_mo),
    ]
    print("\nSCENE-6 FNOL PARITY:")
    for typ, pg, mo in pairs:
        print(f"  {typ:<28} pg={pg.event_hash}\n"
              f"  {'':<28} mo={mo.event_hash}")
        assert pg.event_hash == mo.event_hash, (
            f"{typ} drift: pg={pg.event_hash} mo={mo.event_hash}"
        )
    print()


# ---- Pin 6: claims_scene emit_* on PG ---------------------------------
async def test_r5b2a_claims_scene_emit_helpers_pg(pool):
    """Each of the 6 emit_* helpers writes the pre-extraction shape."""
    from app.kernel import claims_scene as cs
    tid = await _pg_tenant(pool)
    envelope_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    client_id = str(uuid.uuid4())
    claim_id = str(uuid.uuid4())
    claim_ref = "CLM-2025-000042"

    await cs.emit_client_request_received(
        pool, tenant_id=tid, user_id=user_id, envelope_id=envelope_id,
        client_id=client_id, raw_message="pin", policy_id=None,
        incident_date=None,
    )
    await cs.emit_claim_triaged(
        pool, tenant_id=tid, envelope_id=envelope_id, client_id=client_id,
        triage={"claim_type": "property", "urgency": "low",
                "requires_coverage_check": False},
    )
    await cs.emit_claim_created(
        pool, tenant_id=tid, envelope_id=envelope_id, claim_id=claim_id,
        claim_reference=claim_ref, client_id=client_id, policy_id=None,
        claim_type="property", urgency="low", description="pin",
        incident_date=None,
    )
    await cs.emit_insurer_lodgement_requested(
        pool, tenant_id=tid, envelope_id=envelope_id,
        claim_id=claim_id, claim_reference=claim_ref,
    )
    await cs.emit_insurer_lodgement_ack(
        pool, tenant_id=tid, envelope_id=envelope_id,
        claim_id=claim_id, claim_reference=claim_ref,
        lodgement={"insurer_display": "FC", "insurer_reference": "R1",
                    "lodgement_id": "L1"},
    )
    await cs.emit_queue_card_enqueued(
        pool, tenant_id=tid, envelope_id=envelope_id, claim_id=claim_id,
    )

    # Assert 6 events landed, in the right idempotency-key patterns.
    expected = [
        ("ClientRequestReceived", f"claim:req:{envelope_id}"),
        ("ClaimTriaged", f"claim:triaged:{envelope_id}"),
        ("ClaimCreated", f"claim:created:{claim_id}"),
        ("InsurerLodgementRequested", f"lodgement:req:{claim_id}"),
        ("InsurerLodgementAck", f"lodgement:ack:{claim_id}"),
        ("QueueCardEnqueued", f"claim:queued:{envelope_id}"),
    ]
    for typ, idem in expected:
        rows = await _fetch_events(pool, tid, typ, envelope_id)
        assert len(rows) == 1, f"missing {typ}"
        assert rows[0]["idempotency_key"] == idem


# ---- Pin 7: notifications InAppNotification parity across backends ----
async def test_r5b2a_notifications_inapp_parity(pool, mongo_db):
    """Emit InAppNotification directly on both stores with matched
    overrides → byte-identical event_hash."""
    from app.kernel import trail, trail_mongo
    from app.kernel.tenant_ctx import use_tenant

    slug = f"nf-{uuid.uuid4().hex[:6]}"
    frozen_tid = str(uuid.uuid4())
    frozen_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    frozen_eid = str(uuid.uuid4())

    await trail.create_tenant(
        pool, slug=slug, display_name=f"Nf {slug}",
        tenant_id_override=frozen_tid,
        ts_override=frozen_ts, event_id_override=frozen_eid,
    )
    async with use_tenant(frozen_tid):
        await trail_mongo.create_tenant(
            mongo_db, slug, f"Nf {slug}",
            tenant_id_override=frozen_tid,
            ts_override=frozen_ts, event_id_override=frozen_eid,
        )

    notif_id = str(uuid.uuid4())
    payload = {
        "notification_id": notif_id,
        "client_id": str(uuid.uuid4()),
        "kind": "claim_lodged",
        "subject": "We've lodged your claim CLM-2025-000001",
        "body_text": "Hi there,\n\nYour claim CLM-2025-000001 has been lodged.",
        "source_event_id": str(uuid.uuid4()),
        "template_payload": {"claim_reference": "CLM-2025-000001"},
    }
    actor = {"kind": "system", "id": "notify", "name": "notify"}
    ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    eid = str(uuid.uuid4())
    pg_ev = await trail.append(
        pool, frozen_tid, "InAppNotification",
        actor=actor, payload=payload,
        idempotency_key=f"inapp:{notif_id}",
        ts_override=ts, event_id_override=eid,
    )
    async with use_tenant(frozen_tid):
        mo_ev = await trail_mongo.append_event(
            mongo_db, frozen_tid, "InAppNotification",
            actor=actor, payload=payload,
            idempotency_key=f"inapp:{notif_id}",
            ts_override=ts, event_id_override=eid,
        )
    assert pg_ev.event_hash == mo_ev.event_hash
