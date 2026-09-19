"""Phase R5c-1b-β — client_360 kernel extract-first pins.

Locks observable behaviour of `kernel/client_360.py` on both
backends after the extraction from `routes/client_360.py`. PG path
is the byte-verbatim pre-extraction inline SQL; Mongo path uses
`TenantSafeDatabase` under `use_tenant(tid)`. Pins here assert
row/count/shape parity across backends given identical seed input.

Coverage
--------
- `list_all_clients_with_policy_counts`
    * PG returns rows sorted by display_name with correct policy_count.
    * Mongo returns rows with matching shape + counts.
- `get_client_header`
    * PG returns header with active_policies (managed+tracked only)
      and open_matters (status != 'closed').
    * PG returns None for an unknown client.
    * Mongo returns matching header + counts.
    * Mongo returns None for an unknown client.
- `get_client_timeline`
    * PG returns (total, rows) for events keyed by payload.client_id
      OR actor.id, sorted by seq DESC, honouring limit/offset.
    * Mongo returns matching total + rows on the same seeded events.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.tenant_safe import wrap as _wrap_safe
from app.kernel import client_360, trail, trail_mongo
from app.kernel.tenant_ctx import use_tenant


pytestmark = pytest.mark.asyncio


# ---- shared seeders -------------------------------------------------

async def _pg_tenant(pool) -> str:
    slug = f"c360-{uuid.uuid4().hex[:6]}"
    t, _ = await trail.create_tenant(pool, slug=slug, display_name=slug)
    return t.tenant_id


async def _matched_tenant(pool, mongo_db) -> str:
    """Create a tenant on both stores with the same tenant_id so the
    kernel dispatches over the same identifier."""
    slug = f"c360-{uuid.uuid4().hex[:6]}"
    tid = str(uuid.uuid4())
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    eid = str(uuid.uuid4())
    await trail.create_tenant(
        pool, slug=slug, display_name=slug,
        tenant_id_override=tid, ts_override=ts, event_id_override=eid,
    )
    async with use_tenant(tid):
        await trail_mongo.create_tenant(
            mongo_db, slug, slug,
            tenant_id_override=tid, ts_override=ts, event_id_override=eid,
        )
    return tid


async def _seed_pg_clients_and_policies(
    pool, tid: str, spec: list[dict],
) -> None:
    """spec: list of {client_id, display_name, industry, domicile,
    jurisdiction_code, policies: [{policy_id, status}]}."""
    now = datetime.now(timezone.utc)
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tid,
            )
            for s in spec:
                await c.execute(
                    "INSERT INTO clients "
                    "  (client_id, tenant_id, display_name, industry, "
                    "   domicile, jurisdiction_code) "
                    "VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6)",
                    s["client_id"], tid, s["display_name"],
                    s.get("industry", "construction"),
                    s.get("domicile", "AU"),
                    s.get("jurisdiction_code", "AU"),
                )
                for p in s.get("policies", []):
                    await c.execute(
                        "INSERT INTO policies "
                        "  (policy_id, tenant_id, client_id, status, "
                        "   product, insurer_display, inception, expiry, "
                        "   premium_amount, premium_currency, schedule) "
                        "VALUES ($1::uuid, $2::uuid, $3::uuid, $4, "
                        "        'public_liability', 'Fictional Carrier', "
                        "        $5, $6, 10000, 'AUD', $7::jsonb)",
                        p["policy_id"], tid, s["client_id"], p["status"],
                        now, now + timedelta(days=365),
                        json.dumps({"cover_types": ["public_liability"]}),
                    )


async def _seed_mongo_clients_and_policies(
    mongo_db, tid: str, spec: list[dict],
) -> None:
    safe = _wrap_safe(mongo_db)
    async with use_tenant(tid):
        for s in spec:
            await safe.clients.insert_one({
                "client_id": s["client_id"],
                "display_name": s["display_name"],
                "industry": s.get("industry", "construction"),
                "domicile": s.get("domicile", "AU"),
                "jurisdiction_code": s.get("jurisdiction_code", "AU"),
            })
            for p in s.get("policies", []):
                await safe.policies.insert_one({
                    "policy_id": p["policy_id"],
                    "client_id": s["client_id"],
                    "status": p["status"],
                    "product": "public_liability",
                    "insurer_display": "Fictional Carrier",
                })


async def _seed_pg_matter(
    pool, tid: str, client_id: str, matter_id: str, status: str,
) -> None:
    """Emit a MatterCreated event — the in-transaction projection in
    `trail._project` populates `proj_matters` with the given status."""
    await trail.append(
        pool, tid, "MatterCreated",
        actor={"kind": "system", "id": "pin", "name": "pin"},
        payload={"matter_id": matter_id, "client_id": client_id,
                 "status": status, "subject": "pin"},
        idempotency_key=f"pin:matter:{matter_id}",
    )


async def _seed_mongo_matter(
    mongo_db, tid: str, client_id: str, matter_id: str, status: str,
) -> None:
    """Emit a MatterCreated event — `workflow_mongo.project_event`
    populates `proj_matters` when Mongo trail append lands."""
    async with use_tenant(tid):
        await trail_mongo.append_event(
            mongo_db, tid, "MatterCreated",
            actor={"kind": "system", "id": "pin", "name": "pin"},
            payload={"matter_id": matter_id, "client_id": client_id,
                     "status": status, "subject": "pin"},
            idempotency_key=f"pin:mmatter:{matter_id}",
        )


async def _append_pg_event(
    pool, tid: str, typ: str, actor: dict, payload: dict, idem: str,
) -> None:
    await trail.append(
        pool, tid, typ, actor=actor, payload=payload,
        idempotency_key=idem,
    )


async def _append_mongo_event(
    mongo_db, tid: str, typ: str, actor: dict, payload: dict, idem: str,
) -> None:
    async with use_tenant(tid):
        await trail_mongo.append_event(
            mongo_db, tid, typ, actor=actor, payload=payload,
            idempotency_key=idem,
        )


# ---- Pin 1: PG list_all_clients_with_policy_counts ------------------
async def test_pin_pg_list_clients_shape_and_counts(pool):
    tid = await _pg_tenant(pool)
    c1, c2 = str(uuid.uuid4()), str(uuid.uuid4())
    await _seed_pg_clients_and_policies(pool, tid, [
        {"client_id": c1, "display_name": "Alpha Pty",
         "policies": [{"policy_id": str(uuid.uuid4()), "status": "managed"},
                      {"policy_id": str(uuid.uuid4()), "status": "tracked"}]},
        {"client_id": c2, "display_name": "Bravo Ltd",
         "policies": [{"policy_id": str(uuid.uuid4()), "status": "managed"}]},
    ])
    rows = await client_360.list_all_clients_with_policy_counts(pool, tid)
    assert [r["display_name"] for r in rows] == ["Alpha Pty", "Bravo Ltd"]
    counts = {r["display_name"]: int(r["policy_count"]) for r in rows}
    assert counts == {"Alpha Pty": 2, "Bravo Ltd": 1}
    for r in rows:
        assert set(r.keys()) >= {
            "client_id", "display_name", "industry",
            "domicile", "jurisdiction_code", "policy_count",
        }


# ---- Pin 2: Mongo list_all_clients_with_policy_counts ---------------
async def test_pin_mongo_list_clients_shape_and_counts(pool, mongo_db):
    tid = await _matched_tenant(pool, mongo_db)
    c1, c2 = str(uuid.uuid4()), str(uuid.uuid4())
    spec = [
        {"client_id": c1, "display_name": "Alpha Pty",
         "policies": [{"policy_id": str(uuid.uuid4()), "status": "managed"},
                      {"policy_id": str(uuid.uuid4()), "status": "tracked"}]},
        {"client_id": c2, "display_name": "Bravo Ltd",
         "policies": [{"policy_id": str(uuid.uuid4()), "status": "managed"}]},
    ]
    await _seed_mongo_clients_and_policies(mongo_db, tid, spec)
    rows = await client_360.list_all_clients_with_policy_counts(mongo_db, tid)
    assert [r["display_name"] for r in rows] == ["Alpha Pty", "Bravo Ltd"]
    counts = {r["display_name"]: int(r["policy_count"]) for r in rows}
    assert counts == {"Alpha Pty": 2, "Bravo Ltd": 1}


# ---- Pin 3: PG get_client_header — counts + not-found ---------------
async def test_pin_pg_get_client_header_and_missing(pool):
    tid = await _pg_tenant(pool)
    cid = str(uuid.uuid4())
    await _seed_pg_clients_and_policies(pool, tid, [
        {"client_id": cid, "display_name": "Header Pty",
         "policies": [
             {"policy_id": str(uuid.uuid4()), "status": "managed"},
             {"policy_id": str(uuid.uuid4()), "status": "managed"},
             {"policy_id": str(uuid.uuid4()), "status": "tracked"},
             {"policy_id": str(uuid.uuid4()), "status": "lapsed"},
         ]},
    ])
    m1, m2, m3 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    await _seed_pg_matter(pool, tid, cid, m1, "open")
    await _seed_pg_matter(pool, tid, cid, m2, "in_progress")
    await _seed_pg_matter(pool, tid, cid, m3, "closed")

    hdr = await client_360.get_client_header(pool, tid, cid)
    assert hdr is not None
    assert hdr["display_name"] == "Header Pty"
    assert hdr["active_policies"] == 3  # 2 managed + 1 tracked (lapsed excluded)
    assert hdr["open_matters"] == 2     # 2 non-closed

    missing = await client_360.get_client_header(pool, tid, str(uuid.uuid4()))
    assert missing is None


# ---- Pin 4: Mongo get_client_header — counts + not-found ------------
async def test_pin_mongo_get_client_header_and_missing(pool, mongo_db):
    tid = await _matched_tenant(pool, mongo_db)
    cid = str(uuid.uuid4())
    await _seed_mongo_clients_and_policies(mongo_db, tid, [
        {"client_id": cid, "display_name": "Header Pty",
         "policies": [
             {"policy_id": str(uuid.uuid4()), "status": "managed"},
             {"policy_id": str(uuid.uuid4()), "status": "managed"},
             {"policy_id": str(uuid.uuid4()), "status": "tracked"},
             {"policy_id": str(uuid.uuid4()), "status": "lapsed"},
         ]},
    ])
    await _seed_mongo_matter(mongo_db, tid, cid, str(uuid.uuid4()), "open")
    await _seed_mongo_matter(mongo_db, tid, cid, str(uuid.uuid4()), "in_progress")
    await _seed_mongo_matter(mongo_db, tid, cid, str(uuid.uuid4()), "closed")

    hdr = await client_360.get_client_header(mongo_db, tid, cid)
    assert hdr is not None
    assert hdr["display_name"] == "Header Pty"
    assert hdr["active_policies"] == 3
    assert hdr["open_matters"] == 2

    missing = await client_360.get_client_header(mongo_db, tid, str(uuid.uuid4()))
    assert missing is None


# ---- Pin 5: PG get_client_timeline — payload.client_id + actor.id ---
async def test_pin_pg_get_client_timeline_matches_both_keys(pool):
    tid = await _pg_tenant(pool)
    cid = str(uuid.uuid4())
    # Event keyed via payload.client_id
    await _append_pg_event(
        pool, tid, "ClientChangeRequested",
        actor={"kind": "human", "id": str(uuid.uuid4()), "name": "u"},
        payload={"client_id": cid, "envelope_id": str(uuid.uuid4())},
        idem=f"pin:tl:1:{cid}",
    )
    # Event keyed via actor.id (client-actor variant)
    await _append_pg_event(
        pool, tid, "ClientNoteAppended",
        actor={"kind": "client", "id": cid, "name": "client"},
        payload={"note": "hello"},
        idem=f"pin:tl:2:{cid}",
    )
    # Unrelated event that should NOT surface
    await _append_pg_event(
        pool, tid, "UnrelatedNoise",
        actor={"kind": "system", "id": "noise", "name": "noise"},
        payload={"client_id": str(uuid.uuid4())},
        idem=f"pin:tl:noise:{cid}",
    )

    total, rows = await client_360.get_client_timeline(
        pool, tid, cid, limit=50, offset=0,
    )
    assert total == 2
    assert [r["type"] for r in rows] == [
        "ClientNoteAppended", "ClientChangeRequested",
    ]  # sort by seq DESC → most recent first
    for r in rows:
        assert set(r.keys()) >= {"event_id", "seq", "ts", "type",
                                 "actor", "payload"}

    # Offset/limit honoured.
    total2, rows2 = await client_360.get_client_timeline(
        pool, tid, cid, limit=1, offset=1,
    )
    assert total2 == 2
    assert len(rows2) == 1
    assert rows2[0]["type"] == "ClientChangeRequested"


# ---- Pin 6: Mongo get_client_timeline — same shape ------------------
async def test_pin_mongo_get_client_timeline_matches_both_keys(pool, mongo_db):
    tid = await _matched_tenant(pool, mongo_db)
    cid = str(uuid.uuid4())
    await _append_mongo_event(
        mongo_db, tid, "ClientChangeRequested",
        actor={"kind": "human", "id": str(uuid.uuid4()), "name": "u"},
        payload={"client_id": cid, "envelope_id": str(uuid.uuid4())},
        idem=f"pin:mtl:1:{cid}",
    )
    await _append_mongo_event(
        mongo_db, tid, "ClientNoteAppended",
        actor={"kind": "client", "id": cid, "name": "client"},
        payload={"note": "hello"},
        idem=f"pin:mtl:2:{cid}",
    )
    await _append_mongo_event(
        mongo_db, tid, "UnrelatedNoise",
        actor={"kind": "system", "id": "noise", "name": "noise"},
        payload={"client_id": str(uuid.uuid4())},
        idem=f"pin:mtl:noise:{cid}",
    )

    total, rows = await client_360.get_client_timeline(
        mongo_db, tid, cid, limit=50, offset=0,
    )
    assert total == 2
    assert [r["type"] for r in rows] == [
        "ClientNoteAppended", "ClientChangeRequested",
    ]

    total2, rows2 = await client_360.get_client_timeline(
        mongo_db, tid, cid, limit=1, offset=1,
    )
    assert total2 == 2
    assert len(rows2) == 1
    assert rows2[0]["type"] == "ClientChangeRequested"
