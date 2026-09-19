"""Phase 1 done-when tests — the trAIl ledger."""
from __future__ import annotations

import asyncio
import copy
import json
import uuid

import pytest

from app.kernel.trail import append, create_tenant, event_to_json, read_events
from app.kernel.verifier import verify_events, verify_export

pytestmark = pytest.mark.asyncio


# ---------- helpers -----------------------------------------------------------

def _matter(status="new", subject="Test matter") -> dict:
    return {
        "matter_id": str(uuid.uuid4()),
        "client_id": str(uuid.uuid4()),
        "status": status,
        "subject": subject,
    }


async def _make_tenant(pool, slug="acme"):
    return await create_tenant(pool, slug=slug, display_name=slug.title() + " Broking")


# ---------- 1. happy path -----------------------------------------------------

async def test_happy_path_100_events_verifier_passes(pool):
    tenant, genesis = await _make_tenant(pool, "acme")
    assert genesis.type == "TenantGenesis"
    assert tenant.genesis_hash == genesis.event_hash

    matter_id = str(uuid.uuid4())
    client_id = str(uuid.uuid4())
    await append(
        pool, tenant.tenant_id, "MatterCreated",
        actor={"kind": "human", "id": "u1", "name": "Broker"},
        payload={"matter_id": matter_id, "client_id": client_id,
                 "status": "new", "subject": "Fleet cover"},
        idempotency_key=f"mc:{matter_id}",
    )
    # 98 more events (mix of status changes and generic notes)
    for i in range(98):
        if i % 3 == 0:
            await append(
                pool, tenant.tenant_id, "MatterStatusChanged",
                actor={"kind": "agent", "id": "gAIte", "name": "policy"},
                payload={"matter_id": matter_id, "status": f"stage-{i}"},
                idempotency_key=f"msc:{matter_id}:{i}",
            )
        else:
            await append(
                pool, tenant.tenant_id, "NoteRecorded",
                actor={"kind": "human", "id": "u1", "name": "Broker"},
                payload={"seq_hint": i, "text": f"note #{i}"},
                idempotency_key=f"note:{matter_id}:{i}",
            )

    _, events = await read_events(pool, "acme")
    assert len(events) == 100  # genesis + matter created + 98 more

    result = verify_events([event_to_json(e) for e in events])
    assert result.ok, result.reason
    assert result.n_events == 100
    assert result.root_hash == events[-1].event_hash


# ---------- 2 & 3. append-only triggers ---------------------------------------

async def test_events_update_denied(pool):
    tenant, _ = await _make_tenant(pool, "trg-u")
    ev = await append(
        pool, tenant.tenant_id, "NoteRecorded",
        actor={"kind": "system", "id": "t", "name": "t"},
        payload={"x": 1}, idempotency_key="k1",
    )
    async with pool.acquire() as c:
        await c.execute("SELECT set_config('app.tenant_id', $1, false)", tenant.tenant_id)
        with pytest.raises(Exception, match="append-only"):
            await c.execute(
                "UPDATE events SET payload = '{}'::jsonb WHERE event_id = $1",
                ev.event_id,
            )


async def test_events_delete_denied(pool):
    tenant, _ = await _make_tenant(pool, "trg-d")
    ev = await append(
        pool, tenant.tenant_id, "NoteRecorded",
        actor={"kind": "system", "id": "t", "name": "t"},
        payload={"x": 1}, idempotency_key="k1",
    )
    async with pool.acquire() as c:
        await c.execute("SELECT set_config('app.tenant_id', $1, false)", tenant.tenant_id)
        with pytest.raises(Exception, match="append-only"):
            await c.execute("DELETE FROM events WHERE event_id = $1", ev.event_id)


# ---------- 4. RLS cross-tenant negative --------------------------------------

async def test_rls_blocks_cross_tenant_reads(pool):
    a, _ = await _make_tenant(pool, "tenant-a")
    b, _ = await _make_tenant(pool, "tenant-b")
    # Insert an event into B
    ev_b = await append(
        pool, b.tenant_id, "NoteRecorded",
        actor={"kind": "system", "id": "s", "name": "s"},
        payload={"secret": "b-only"}, idempotency_key="b-secret",
    )
    async with pool.acquire() as c:
        async with c.transaction():
            # Set tenant A and try to read anything from B
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", a.tenant_id)
            rows = await c.fetch(
                "SELECT event_id FROM events WHERE tenant_id = $1", b.tenant_id
            )
            assert rows == [], "RLS leaked cross-tenant rows"
            # And a direct fetch by event_id also denied
            rows = await c.fetch(
                "SELECT event_id FROM events WHERE event_id = $1", ev_b.event_id
            )
            assert rows == [], "RLS leaked cross-tenant by event_id"


# ---------- 5. idempotency violation ------------------------------------------

async def test_idempotency_violation(pool):
    tenant, _ = await _make_tenant(pool, "idem")
    ev1 = await append(
        pool, tenant.tenant_id, "NoteRecorded",
        actor={"kind": "system", "id": "s", "name": "s"},
        payload={"n": 1}, idempotency_key="dup",
    )
    with pytest.raises(Exception):
        await append(
            pool, tenant.tenant_id, "NoteRecorded",
            actor={"kind": "system", "id": "s", "name": "s"},
            payload={"n": 2}, idempotency_key="dup",
        )
    _, events = await read_events(pool, "idem")
    # genesis + ev1 only
    assert len(events) == 2
    assert events[-1].event_id == ev1.event_id


# ---------- 6. tamper detection in verifier -----------------------------------

async def test_tamper_detection_in_export(pool):
    tenant, _ = await _make_tenant(pool, "tamper")
    for i in range(5):
        await append(
            pool, tenant.tenant_id, "NoteRecorded",
            actor={"kind": "system", "id": "s", "name": "s"},
            payload={"i": i}, idempotency_key=f"n:{i}",
        )
    _, events = await read_events(pool, "tamper")
    export = {
        "schema_version": "trail/v1",
        "tenant": {"tenant_id": tenant.tenant_id, "slug": "tamper",
                    "genesis_hash": tenant.genesis_hash},
        "events": [event_to_json(e) for e in events],
    }
    # Baseline verify OK
    ok = verify_export(export)
    assert ok.ok

    # Mutate the payload_jcs of event at index 3 (seq 4)
    tampered = copy.deepcopy(export)
    target_idx = 3
    tampered["events"][target_idx]["payload_jcs"] = '{"i":999}'
    bad = verify_export(tampered)
    assert not bad.ok
    assert bad.failure_seq == tampered["events"][target_idx]["seq"]
    assert bad.failure_event_id == tampered["events"][target_idx]["event_id"]


# ---------- 7. genesis chain match --------------------------------------------

async def test_genesis_hash_matches_first_event(pool):
    tenant, genesis = await _make_tenant(pool, "gen")
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT genesis_hash FROM tenants WHERE tenant_id = $1", tenant.tenant_id
        )
    assert row["genesis_hash"] == genesis.event_hash
    assert genesis.prev_hash == "0" * 64
    assert genesis.seq == 1


# ---------- 8. concurrent inserts, chain integrity ----------------------------

async def test_concurrent_inserts_chain_intact(pool):
    tenant, _ = await _make_tenant(pool, "concur")

    async def do(i: int):
        await append(
            pool, tenant.tenant_id, "NoteRecorded",
            actor={"kind": "system", "id": "s", "name": "s"},
            payload={"i": i}, idempotency_key=f"c:{i}",
        )

    await asyncio.gather(*[do(i) for i in range(10)])

    _, events = await read_events(pool, "concur")
    assert len(events) == 11  # genesis + 10
    # Chain must be intact.
    result = verify_events([event_to_json(e) for e in events])
    assert result.ok, result.reason
    # seqs are dense 1..11
    assert [e.seq for e in events] == list(range(1, 12))
