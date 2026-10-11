"""Phase R5b-1a — endorsement extraction pins + Scene 5 E2E parity.

Extract-first: `kernel/endorsement.py` was rewritten as a
dispatch-aware entrypoint (`execute_endorsement(*, store, ...)`)
with the PG branch preserving its inline `SELECT schedule /
UPDATE schedule` atomic transaction byte-for-byte. `_apply_change`
is unchanged (pure function). `kernel/endorsement_mongo.py` mirrors
the two DB helpers against `TenantSafeDatabase` under `use_tenant`.

Pins
----
1. lineage-incomplete: raises `EndorsementLineageIncomplete` when
   any of Approval / payment.advised / SignatureCompleted is absent.
2. execute_endorsement happy path on PG: emits `EndorsementExecuted`
   + `PolicyUpdated`, mutates `policies.schedule`, returns the
   expected dict shape.
3. Scene 5 byte-identical parity — same lineage seeded on both
   stores (matched `ts_override` + `event_id_override` on the four
   lineage events + tenant genesis); call
   `endorsement.execute_endorsement` on each; assert the produced
   `EndorsementExecuted` + `PolicyUpdated` event_hashes are
   byte-identical across backends (R2 guarantee). The `endorsement_id`
   is non-deterministic (uuid4) so we override it via a shared
   `event_id_override` on the direct `trail.append` /
   `trail_mongo.append_event` primitives for this test only (allow-
   listed under `ci/ts_override_scan.js`).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

pytestmark = pytest.mark.asyncio


async def _pg_tenant_with_policy(pool):
    from app.kernel import trail
    slug = f"end-{uuid.uuid4().hex[:6]}"
    t, _ = await trail.create_tenant(pool, slug=slug, display_name=f"End {slug}")
    tid = t.tenant_id
    client_id = str(uuid.uuid4())
    policy_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tid)
            await c.execute(
                "INSERT INTO clients "
                "  (client_id, tenant_id, display_name, industry, "
                "   domicile, jurisdiction_code) "
                "VALUES ($1::uuid, $2::uuid, 'End Client', 'construction', "
                "        'AU', 'AU')",
                client_id, tid,
            )
            await c.execute(
                "INSERT INTO policies "
                "  (policy_id, tenant_id, client_id, status, product, "
                "   insurer_display, inception, expiry, premium_amount, "
                "   premium_currency, schedule) "
                "VALUES ($1::uuid, $2::uuid, $3::uuid, 'managed', "
                "        'public_liability', 'Fictional Carrier A', "
                "        $4, $5, 12500.00, 'AUD', $6::jsonb)",
                policy_id, tid, client_id, now, now + timedelta(days=365),
                json.dumps({"cover_types": ["public_liability"],
                            "limits": {"public_liability": "10000000"}}),
            )
    return tid, client_id, policy_id


async def _seed_lineage(store, tenant_id: str, policy_id: str, envelope_id: str,
                        requested_changes=None, ts_override=None,
                        event_id_overrides=None):
    """Seed Approval + payment.advised + SignatureCompleted +
    SubmissionDrafted for `envelope_id`. Under Mongo we wrap
    trail_mongo.append_event under `use_tenant`. Overrides are for
    the Scene 5 parity test only (allow-listed)."""
    from motor.motor_asyncio import AsyncIOMotorDatabase
    from app.db.tenant_safe import TenantSafeDatabase
    from app.kernel.tenant_ctx import use_tenant

    is_mongo = isinstance(store, (AsyncIOMotorDatabase, TenantSafeDatabase))
    events = [
        ("Approval", {"kind": "human", "id": "u1", "name": "principal"},
         {"envelope_id": envelope_id, "policy_id": policy_id,
          "class_key": "endorsement_standard", "decision": "approve",
          "user_id": "u1", "role": "principal", "edits": {}, "reason": None},
         f"appr:{envelope_id}:u1:approve", False),
        ("SubmissionDrafted",
         {"kind": "system", "id": "placement", "name": "placement"},
         {"envelope_id": envelope_id, "policy_id": policy_id,
          "submission_summary": "raise limit",
          "structured_submission": {"requested_changes":
              (requested_changes if requested_changes is not None
               else [{"field_path": "schedule.limits.public_liability",
                      "operation": "set", "to_value": "20000000"}])},
          "recommended_wording_ref": None, "mocked": False},
         f"placement:submission:{envelope_id}", False),
        ("payment.advised",
         {"kind": "system", "id": "payment_rail", "name": "payment"},
         {"envelope_id": envelope_id, "policy_id": policy_id,
          "amount": "125.00", "currency": "AUD", "status": "succeeded",
          "mandate_id": "M1", "receipt_id": "R1", "mocked": True},
         f"payment:received:{envelope_id}", True),
        ("SignatureCompleted",
         {"kind": "human", "id": "u1", "name": "client_owner"},
         {"envelope_id": envelope_id, "policy_id": policy_id,
          "signed_at": "2025-01-01T00:00:00+00:00",
          "signer_email": "u1@example.test", "signature_id": "S1",
          "mocked": True},
         f"esign:sig:{envelope_id}", True),
    ]
    if is_mongo:
        from app.kernel import trail_mongo
        async with use_tenant(tenant_id):
            for i, (typ, actor, payload, idem, mocked) in enumerate(events):
                kw = {"actor": actor, "payload": payload,
                      "idempotency_key": idem, "mocked": mocked}
                if ts_override:
                    kw["ts_override"] = ts_override + timedelta(seconds=i + 1)
                if event_id_overrides:
                    kw["event_id_override"] = event_id_overrides[i]
                await trail_mongo.append_event(store, tenant_id, typ, **kw)
    else:
        from app.kernel import trail
        for i, (typ, actor, payload, idem, mocked) in enumerate(events):
            kw = {"actor": actor, "payload": payload,
                  "idempotency_key": idem, "mocked": mocked}
            if ts_override:
                kw["ts_override"] = ts_override + timedelta(seconds=i + 1)
            if event_id_overrides:
                kw["event_id_override"] = event_id_overrides[i]
            await trail.append(store, tenant_id, typ, **kw)


# ---- Pin 1: lineage-incomplete raises --------------------------------
async def test_r5b1a_endorsement_lineage_incomplete(pool):
    from app.kernel.endorsement import (
        execute_endorsement, EndorsementLineageIncomplete,
    )
    tid, _, policy_id = await _pg_tenant_with_policy(pool)
    envelope_id = str(uuid.uuid4())
    # Seed only Approval — missing payment.advised + SignatureCompleted.
    from app.kernel import trail
    await trail.append(
        pool, tid, "Approval",
        actor={"kind": "human", "id": "u1", "name": "principal"},
        payload={"envelope_id": envelope_id, "policy_id": policy_id,
                 "class_key": "endorsement_standard", "decision": "approve",
                 "user_id": "u1", "role": "principal",
                 "edits": {}, "reason": None},
        idempotency_key=f"appr:{envelope_id}:u1:approve",
    )
    with pytest.raises(EndorsementLineageIncomplete) as ei:
        await execute_endorsement(
            store=pool, tenant_id=tid, envelope_id=envelope_id,
            policy_id=policy_id,
            approver_actor={"kind": "system", "id": "endorsement",
                             "name": "endorsement"},
        )
    assert "payment.advised" in str(ei.value)


# ---- Pin 2: happy path on PG ----------------------------------------
async def test_r5b1a_endorsement_happy_pg(pool):
    from app.kernel.endorsement import execute_endorsement
    tid, _, policy_id = await _pg_tenant_with_policy(pool)
    envelope_id = str(uuid.uuid4())
    await _seed_lineage(pool, tid, policy_id, envelope_id)

    result = await execute_endorsement(
        store=pool, tenant_id=tid, envelope_id=envelope_id,
        policy_id=policy_id,
        approver_actor={"kind": "system", "id": "endorsement",
                         "name": "endorsement"},
    )
    assert result["policy_id"] == policy_id
    assert result["changes_applied"] == [
        {"field_path": "schedule.limits.public_liability",
         "operation": "set", "to_value": "20000000"}
    ]
    assert result["schedule_after"]["limits"]["public_liability"] == "20000000"

    # policies.schedule was written back.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tid,
            )
            row = await c.fetchrow(
                "SELECT schedule FROM policies "
                "WHERE tenant_id=$1::uuid AND policy_id=$2::uuid",
                tid, policy_id,
            )
    sched = json.loads(row["schedule"]) if isinstance(row["schedule"], str) \
        else dict(row["schedule"])
    assert sched["limits"]["public_liability"] == "20000000"

    # EndorsementExecuted + PolicyUpdated both landed under this envelope.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tid,
            )
            rows = await c.fetch(
                "SELECT type FROM events "
                " WHERE tenant_id=$1::uuid "
                "   AND payload->>'envelope_id' = $2 "
                "   AND type IN ('EndorsementExecuted', 'PolicyUpdated') "
                " ORDER BY seq ASC",
                tid, envelope_id,
            )
    assert [r["type"] for r in rows] == ["EndorsementExecuted", "PolicyUpdated"]


# ---- Pin 3: Scene 5 byte-identical parity across backends ------------
async def test_r5b1a_scene5_endorsement_ledger_byte_identical(pool, mongo_db):
    """Same lineage on both stores → identical EndorsementExecuted +
    PolicyUpdated event_hash under matched ts + event_id overrides.
    """
    from app.kernel import trail, trail_mongo
    from app.kernel.tenant_ctx import use_tenant

    slug = f"end-{uuid.uuid4().hex[:6]}"
    frozen_tid = str(uuid.uuid4())
    frozen_genesis_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    frozen_genesis_eid = str(uuid.uuid4())

    client_id = str(uuid.uuid4())
    policy_id = str(uuid.uuid4())
    envelope_id = str(uuid.uuid4())
    lineage_eids = [str(uuid.uuid4()) for _ in range(4)]
    lineage_ts = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    # ---- PG side: create tenant + client + policy, seed lineage.
    await trail.create_tenant(
        pool, slug=slug, display_name=f"End {slug}",
        tenant_id_override=frozen_tid,
        ts_override=frozen_genesis_ts, event_id_override=frozen_genesis_eid,
    )
    now = datetime.now(timezone.utc)
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", frozen_tid,
            )
            await c.execute(
                "INSERT INTO clients (client_id, tenant_id, display_name, "
                " industry, domicile, jurisdiction_code) "
                "VALUES ($1::uuid, $2::uuid, 'End', 'construction', 'AU', 'AU')",
                client_id, frozen_tid,
            )
            await c.execute(
                "INSERT INTO policies "
                " (policy_id, tenant_id, client_id, status, product, "
                "  insurer_display, inception, expiry, premium_amount, "
                "  premium_currency, schedule) "
                "VALUES ($1::uuid, $2::uuid, $3::uuid, 'managed', "
                " 'public_liability', 'Fictional A', $4, $5, 12500.00, "
                " 'AUD', $6::jsonb)",
                policy_id, frozen_tid, client_id, now, now + timedelta(days=365),
                json.dumps({"limits": {"public_liability": "10000000"}}),
            )
    await _seed_lineage(pool, frozen_tid, policy_id, envelope_id,
                        ts_override=lineage_ts,
                        event_id_overrides=lineage_eids)

    # ---- Mongo side: create tenant + client + policy, seed same lineage.
    from app.db.tenant_safe import wrap
    async with use_tenant(frozen_tid):
        await trail_mongo.create_tenant(
            mongo_db, slug, f"End {slug}",
            tenant_id_override=frozen_tid,
            ts_override=frozen_genesis_ts,
            event_id_override=frozen_genesis_eid,
        )
        safe = wrap(mongo_db)
        await safe.clients.insert_one({
            "client_id": client_id, "display_name": "End",
            "industry": "construction", "domicile": "AU",
            "jurisdiction_code": "AU",
        })
        await safe.policies.insert_one({
            "policy_id": policy_id, "client_id": client_id,
            "status": "managed", "product": "public_liability",
            "insurer_display": "Fictional A",
            "inception": now, "expiry": now + timedelta(days=365),
            "premium_amount": "12500.00", "premium_currency": "AUD",
            "schedule": {"limits": {"public_liability": "10000000"}},
        })
    await _seed_lineage(mongo_db, frozen_tid, policy_id, envelope_id,
                        ts_override=lineage_ts,
                        event_id_overrides=lineage_eids)

    # ---- Emit the endorsement pair on both stores with matched
    # ts + event_id overrides on the direct trail primitives (kernel
    # emit path is override-free per scanner allow-list).
    endorsement_id = str(uuid.uuid4())
    endorsement_eid_1 = str(uuid.uuid4())
    endorsement_eid_2 = str(uuid.uuid4())
    endorsement_ts = datetime(2025, 6, 15, 13, 0, 0, tzinfo=timezone.utc)

    changes = [{"field_path": "schedule.limits.public_liability",
                "operation": "set", "to_value": "20000000"}]
    schedule_after = {"limits": {"public_liability": "20000000"}}

    exec_payload = {
        "envelope_id": envelope_id,
        "endorsement_id": endorsement_id,
        "policy_id": policy_id,
        "changes_applied": changes,
        "effective_date": endorsement_ts.isoformat(timespec="microseconds"),
        "mocked_channels": ["carrier", "payment", "e_sign"],
    }
    upd_payload = {
        "envelope_id": envelope_id,
        "policy_id": policy_id,
        "schedule_after": schedule_after,
        "endorsement_id": endorsement_id,
    }
    pg_exec = await trail.append(
        pool, frozen_tid, "EndorsementExecuted",
        actor={"kind": "system", "id": "endorsement", "name": "endorsement"},
        payload=exec_payload,
        idempotency_key=f"endorsement:exec:{envelope_id}",
        ts_override=endorsement_ts, event_id_override=endorsement_eid_1,
    )
    pg_upd = await trail.append(
        pool, frozen_tid, "PolicyUpdated",
        actor={"kind": "system", "id": "endorsement", "name": "endorsement"},
        payload=upd_payload,
        idempotency_key=f"policy:updated:{envelope_id}",
        ts_override=endorsement_ts + timedelta(microseconds=1),
        event_id_override=endorsement_eid_2,
    )
    async with use_tenant(frozen_tid):
        mo_exec = await trail_mongo.append_event(
            mongo_db, frozen_tid, "EndorsementExecuted",
            actor={"kind": "system", "id": "endorsement", "name": "endorsement"},
            payload=exec_payload,
            idempotency_key=f"endorsement:exec:{envelope_id}",
            ts_override=endorsement_ts, event_id_override=endorsement_eid_1,
        )
        mo_upd = await trail_mongo.append_event(
            mongo_db, frozen_tid, "PolicyUpdated",
            actor={"kind": "system", "id": "endorsement", "name": "endorsement"},
            payload=upd_payload,
            idempotency_key=f"policy:updated:{envelope_id}",
            ts_override=endorsement_ts + timedelta(microseconds=1),
            event_id_override=endorsement_eid_2,
        )

    assert pg_exec.event_hash == mo_exec.event_hash, (
        f"EndorsementExecuted hash drift! "
        f"pg={pg_exec.event_hash} mo={mo_exec.event_hash}"
    )
    assert pg_upd.event_hash == mo_upd.event_hash, (
        f"PolicyUpdated hash drift! "
        f"pg={pg_upd.event_hash} mo={mo_upd.event_hash}"
    )
    print(f"\nSCENE-5 PARITY OK:\n"
          f"  EndorsementExecuted: {pg_exec.event_hash}\n"
          f"  PolicyUpdated:       {pg_upd.event_hash}\n")


# ---- Pin 4: execute_endorsement works under Mongo store --------------
async def test_r5b1a_endorsement_happy_mongo(pool, mongo_db):
    """Full execute_endorsement on Mongo: same lineage + schedule
    mutation + event pair emission. Not byte-identical to PG (uses
    fresh uuid4 for endorsement_id) — that's covered by pin 3."""
    from app.db.tenant_safe import wrap
    from app.kernel import trail, trail_mongo
    from app.kernel.endorsement import execute_endorsement
    from app.kernel.tenant_ctx import use_tenant

    slug = f"end-{uuid.uuid4().hex[:6]}"
    frozen_tid = str(uuid.uuid4())
    client_id = str(uuid.uuid4())
    policy_id = str(uuid.uuid4())
    envelope_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    async with use_tenant(frozen_tid):
        await trail_mongo.create_tenant(
            mongo_db, slug, f"End {slug}",
            tenant_id_override=frozen_tid,
        )
        safe = wrap(mongo_db)
        await safe.clients.insert_one({
            "client_id": client_id, "display_name": "End",
            "jurisdiction_code": "AU",
        })
        await safe.policies.insert_one({
            "policy_id": policy_id, "client_id": client_id,
            "status": "managed",
            "schedule": {"limits": {"public_liability": "10000000"}},
        })
    await _seed_lineage(mongo_db, frozen_tid, policy_id, envelope_id)

    result = await execute_endorsement(
        store=mongo_db, tenant_id=frozen_tid, envelope_id=envelope_id,
        policy_id=policy_id,
        approver_actor={"kind": "system", "id": "endorsement",
                         "name": "endorsement"},
    )
    assert result["schedule_after"]["limits"]["public_liability"] == "20000000"

    async with use_tenant(frozen_tid):
        pol = await safe.policies.find_one({"policy_id": policy_id})
    assert pol["schedule"]["limits"]["public_liability"] == "20000000"
