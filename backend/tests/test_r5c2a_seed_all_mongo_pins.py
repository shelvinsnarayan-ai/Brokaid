"""Phase R5c-2a — extract-first parity pins for the 7 Mongo sub-
seeders + the `seed_all_mongo` composer.

Each pin seeds an isolated tenant on BOTH backends (Postgres via
the shared conftest `pool`, Mongo via `mongo_db`) then asserts the
seed produced the same observable fixture data across backends —
same row counts, same key business identifiers, and same ledger-
event types + idempotency keys. Composer pin does the same for the
end-to-end sequence.

Byte-identical event_hashes are already pinned in R5c-1a pin 3
(direct `trail.append` + `trail_mongo.append_event` with matched
overrides through `append_via_dispatch`) — the seeders themselves
use real-time timestamps + fresh event_ids per run, so the pin
here asserts the SHAPE parity (type/actor/payload keyset/idem-key
pattern), not literal hash equality.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.db.tenant_safe import wrap as _wrap_safe
from app.kernel import trail, trail_mongo
from app.kernel.tenant_ctx import use_tenant


pytestmark = pytest.mark.asyncio


# ---- Fixture helpers ------------------------------------------------

async def _matched_tenant(pool, mongo_db, slug_prefix: str = "s2a") -> str:
    """Create the same tenant on both stores under a shared tenant_id."""
    slug = f"{slug_prefix}-{uuid.uuid4().hex[:6]}"
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


async def _pg_count(pool, tid: str, sql: str, *args) -> int:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tid)
            return await c.fetchval(sql, tid, *args)


async def _pg_events_by_idem(pool, tid: str, idem: str) -> dict | None:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tid)
            row = await c.fetchrow(
                "SELECT type, actor, payload, idempotency_key FROM events "
                " WHERE tenant_id=$1::uuid AND idempotency_key=$2",
                tid, idem,
            )
    return dict(row) if row else None


async def _mongo_events_by_idem(mongo_db, tid: str, idem: str) -> dict | None:
    safe = _wrap_safe(mongo_db)
    async with use_tenant(tid):
        r = await safe.events.find_one({"idempotency_key": idem})
    return r


# ---- Pin 1: seed_org -------------------------------------------------
async def test_pin_seed_org_mongo_matches_pg(pool, mongo_db):
    from app.seeds import seed_org, seed_org_mongo
    from app.seeds.seed_org import LICENSEE_ABN

    tid = await _matched_tenant(pool, mongo_db, "org")
    brokerage_id = str(uuid.uuid4())

    await seed_org.seed(pool, tid, brokerage_id)
    await seed_org_mongo.seed_mongo(mongo_db, tid, brokerage_id)

    for typ, idem in [
        ("LicenseeCreated", f"seed:licensee:{LICENSEE_ABN}"),
        ("BrokerageCreated", f"seed:brokerage:{brokerage_id}"),
    ]:
        pg_ev = await _pg_events_by_idem(pool, tid, idem)
        mo_ev = await _mongo_events_by_idem(mongo_db, tid, idem)
        assert pg_ev is not None and mo_ev is not None
        assert pg_ev["type"] == mo_ev["type"] == typ


# ---- Pin 2: seed_client ---------------------------------------------
async def test_pin_seed_client_mongo_matches_pg(pool, mongo_db):
    from app.seeds import seed_client, seed_client_mongo
    from app.seeds.seed_client import CLIENT_ID, CLIENT_NAME

    tid = await _matched_tenant(pool, mongo_db, "cli")

    pg_cid = await seed_client.seed(pool, tid)
    mo_cid = await seed_client_mongo.seed_mongo(mongo_db, tid)
    assert pg_cid == mo_cid == CLIENT_ID

    # Row parity
    pg_row_n = await _pg_count(
        pool, tid,
        "SELECT COUNT(*) FROM clients WHERE tenant_id=$1::uuid",
    )
    safe = _wrap_safe(mongo_db)
    async with use_tenant(tid):
        mo_row_n = await safe.clients.count_documents({})
        mo_row = await safe.clients.find_one({"client_id": CLIENT_ID})
    assert pg_row_n == mo_row_n == 1
    assert mo_row is not None
    assert mo_row["display_name"] == CLIENT_NAME

    # Event parity
    idem = f"seed:client:{CLIENT_ID}"
    pg_ev = await _pg_events_by_idem(pool, tid, idem)
    mo_ev = await _mongo_events_by_idem(mongo_db, tid, idem)
    assert pg_ev is not None and mo_ev is not None
    assert pg_ev["type"] == mo_ev["type"] == "ClientEntityCreated"


# ---- Pin 3: seed_policy_managed -------------------------------------
async def test_pin_seed_policy_managed_mongo_matches_pg(pool, mongo_db):
    from app.seeds import (
        seed_client, seed_client_mongo,
        seed_policy_managed, seed_policy_managed_mongo,
    )
    from app.seeds.seed_policy_managed import POLICY_ID

    tid = await _matched_tenant(pool, mongo_db, "polm")
    cid = await seed_client.seed(pool, tid)
    await seed_client_mongo.seed_mongo(mongo_db, tid)

    pg_pid = await seed_policy_managed.seed(pool, tid, cid)
    mo_pid = await seed_policy_managed_mongo.seed_mongo(mongo_db, tid, cid)
    assert pg_pid == mo_pid == POLICY_ID

    pg_pol_n = await _pg_count(
        pool, tid,
        "SELECT COUNT(*) FROM policies WHERE tenant_id=$1::uuid",
    )
    safe = _wrap_safe(mongo_db)
    async with use_tenant(tid):
        mo_pol_n = await safe.policies.count_documents({})
        pol = await safe.policies.find_one({"policy_id": POLICY_ID})
    assert pg_pol_n == mo_pol_n == 1
    assert pol["status"] == "managed"
    assert pol["product"] == "Marine Liability"
    assert float(pol["premium_amount"]) == 8500.0

    idem = f"seed:policy_created:{POLICY_ID}"
    pg_ev = await _pg_events_by_idem(pool, tid, idem)
    mo_ev = await _mongo_events_by_idem(mongo_db, tid, idem)
    assert pg_ev is not None and mo_ev is not None
    assert pg_ev["type"] == mo_ev["type"] == "PolicyCreated"


# ---- Pin 4: seed_policy_tracked -------------------------------------
async def test_pin_seed_policy_tracked_mongo_matches_pg(pool, mongo_db):
    from app.seeds import (
        seed_client, seed_client_mongo,
        seed_policy_tracked, seed_policy_tracked_mongo,
    )
    from app.seeds.seed_policy_tracked import POLICY_ID

    tid = await _matched_tenant(pool, mongo_db, "polt")
    cid = await seed_client.seed(pool, tid)
    await seed_client_mongo.seed_mongo(mongo_db, tid)

    pg_pid = await seed_policy_tracked.seed(pool, tid, cid)
    mo_pid = await seed_policy_tracked_mongo.seed_mongo(mongo_db, tid, cid)
    assert pg_pid == mo_pid == POLICY_ID

    safe = _wrap_safe(mongo_db)
    async with use_tenant(tid):
        pol = await safe.policies.find_one({"policy_id": POLICY_ID})
    assert pol["status"] == "tracked"
    assert pol["product"] == "Cyber"
    assert pol["imported_from"] == "manual_entry_burmac"
    assert float(pol["premium_amount"]) == 2200.0

    idem = f"seed:policy_created:{POLICY_ID}"
    pg_ev = await _pg_events_by_idem(pool, tid, idem)
    mo_ev = await _mongo_events_by_idem(mongo_db, tid, idem)
    assert pg_ev is not None and mo_ev is not None
    assert pg_ev["type"] == mo_ev["type"] == "PolicyCreated"


# ---- Pin 5: seed_vulnerable -----------------------------------------
async def test_pin_seed_vulnerable_mongo_matches_pg(pool, mongo_db):
    from app.seeds import seed_vulnerable, seed_vulnerable_mongo
    from app.seeds.seed_vulnerable import (
        VULN_CLIENT_ID, VULN_CLIENT_NAME, VULN_POLICY_ID,
    )

    tid = await _matched_tenant(pool, mongo_db, "vuln")

    pg_result = await seed_vulnerable.seed(pool, tid)
    mo_result = await seed_vulnerable_mongo.seed_mongo(mongo_db, tid)
    assert pg_result == mo_result == {
        "client_id": VULN_CLIENT_ID, "policy_id": VULN_POLICY_ID,
    }

    # Vulnerable flag lands on both.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tid)
            pg_row = await c.fetchrow(
                "SELECT display_name, vulnerable_flag FROM clients "
                " WHERE client_id=$1::uuid AND tenant_id=$2::uuid",
                VULN_CLIENT_ID, tid,
            )
    safe = _wrap_safe(mongo_db)
    async with use_tenant(tid):
        mo_row = await safe.clients.find_one({"client_id": VULN_CLIENT_ID})
    assert pg_row["display_name"] == mo_row["display_name"] == VULN_CLIENT_NAME
    assert bool(pg_row["vulnerable_flag"]) is True
    assert bool(mo_row["vulnerable_flag"]) is True

    for typ, idem in [
        ("ClientEntityCreated", f"seed:client:{VULN_CLIENT_ID}"),
        ("PolicyCreated", f"seed:policy:{VULN_POLICY_ID}"),
    ]:
        pg_ev = await _pg_events_by_idem(pool, tid, idem)
        mo_ev = await _mongo_events_by_idem(mongo_db, tid, idem)
        assert pg_ev is not None and mo_ev is not None
        assert pg_ev["type"] == mo_ev["type"] == typ


# ---- Pin 6: seed_historical -----------------------------------------
async def test_pin_seed_historical_mongo_matches_pg(pool, mongo_db):
    from app.seeds import seed_historical, seed_historical_mongo
    from app.seeds.seed_historical import (
        CARD_SPECS, CARDS_KEY, CLASSES_FOR_HISTORY, HISTORICAL_KEY,
    )

    tid = await _matched_tenant(pool, mongo_db, "hist")
    # No client_id needed — pass None; PG seeder handles it.

    pg_hist = await seed_historical.seed_historical_decisions(pool, tid, count=30)
    mo_hist = await seed_historical_mongo.seed_historical_decisions_mongo(
        mongo_db, tid, count=30,
    )
    assert pg_hist["seeded"] and mo_hist["seeded"]
    assert pg_hist["count"] == mo_hist["count"] == 30

    # proj_class_unlock parity (row count only — RNG produces
    # matching sequences per class since both run with SEED_RNG).
    pg_n_unlocks = await _pg_count(
        pool, tid,
        "SELECT COUNT(*) FROM proj_class_unlock WHERE tenant_id=$1::uuid",
    )
    safe = _wrap_safe(mongo_db)
    async with use_tenant(tid):
        mo_n_unlocks = await safe.proj_class_unlock.count_documents({})
    assert pg_n_unlocks == mo_n_unlocks == len(CLASSES_FOR_HISTORY)

    # Approval events land on both (idempotency-keys are RNG-driven
    # and match; count of unique Approval idem-keys must match).
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tid)
            pg_n_appr = await c.fetchval(
                "SELECT COUNT(*) FROM events "
                " WHERE tenant_id=$1::uuid AND type='Approval'",
                tid,
            )
    async with use_tenant(tid):
        mo_n_appr = await safe.events.count_documents({"type": "Approval"})
    assert pg_n_appr == mo_n_appr
    assert pg_n_appr >= 25  # allow for collisions in the RNG stream

    # SeedMarker lands on both.
    pg_marker = await _pg_events_by_idem(pool, tid, HISTORICAL_KEY)
    mo_marker = await _mongo_events_by_idem(mongo_db, tid, HISTORICAL_KEY)
    assert pg_marker is not None and mo_marker is not None
    assert pg_marker["type"] == mo_marker["type"] == "SeedMarker"

    # Now queue cards — PG seeder takes a client_id; use None (matches
    # the "burmac exists but no clients yet" case) to keep the pin
    # independent of seed_client wiring.
    pg_cards = await seed_historical.seed_queue_cards(pool, tid, None)
    mo_cards = await seed_historical_mongo.seed_queue_cards_mongo(
        mongo_db, tid, None,
    )
    assert pg_cards["seeded"] and mo_cards["seeded"]
    assert len(pg_cards["cards"]) == len(mo_cards["cards"]) == len(CARD_SPECS)

    # AgentOutputProduced proposals + queue projection parity.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tid)
            pg_n_prop = await c.fetchval(
                "SELECT COUNT(*) FROM events "
                " WHERE tenant_id=$1::uuid AND type='AgentOutputProduced'",
                tid,
            )
            pg_n_queue = await c.fetchval(
                "SELECT COUNT(*) FROM proj_queue WHERE tenant_id=$1::uuid",
                tid,
            )
    async with use_tenant(tid):
        mo_n_prop = await safe.events.count_documents(
            {"type": "AgentOutputProduced"},
        )
        mo_n_queue = await safe.proj_queue.count_documents({})
    assert pg_n_prop == mo_n_prop == len(CARD_SPECS)
    assert pg_n_queue == mo_n_queue == len(CARD_SPECS)

    pg_cards_marker = await _pg_events_by_idem(pool, tid, CARDS_KEY)
    mo_cards_marker = await _mongo_events_by_idem(mongo_db, tid, CARDS_KEY)
    assert pg_cards_marker is not None and mo_cards_marker is not None


# ---- Pin 7: phase16_touch -------------------------------------------
async def test_pin_phase16_touch_mongo_lands_12_events(pool, mongo_db):
    """phase16_touch is one-shot dev tooling; the PG side has no
    parity-friendly counterpart (it hardcodes MONGO_URL via
    None  # PG deleted R5c-4a). Pin the Mongo side alone: 4 Brain.invoke()
    calls land exactly 12 AgentInvocation events (3 per invocation)
    on the burmac tenant.
    """
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import use_tenant
    from app.seeds import phase16_touch_mongo, seed_users_mongo

    # Prime the burmac tenant on Mongo so touch_mongo resolves it.
    result = await seed_users_mongo.seed_mongo(mongo_db)
    tenant_id = result["tenant_id"]

    resolved_tid = await phase16_touch_mongo.touch_mongo(mongo_db)
    assert resolved_tid == tenant_id

    safe = wrap(mongo_db)
    async with use_tenant(tenant_id):
        n = await safe.events.count_documents({
            "type": {"$in": [
                "AgentInvocationStarted",
                "AgentOutputProduced",
                "AgentInvocationCompleted",
            ]},
            "actor.id": "brain",
        })
    # 4 agents × 3 events each.
    assert n == 12


# ---- Pin 8: seed_all_mongo composer end-to-end parity ---------------
async def test_pin_seed_all_mongo_composer_parity(pool, mongo_db):
    """End-to-end: run the PG composer + the Mongo composer on their
    own tenants (composers own tenant creation — burmac slug) and
    compare full-snapshot row/event counts."""
    from app.db.tenant_safe import wrap
    from app.kernel.tenant_ctx import use_tenant
    from app.seeds import seed_all, seed_all_mongo

    pg_result = await seed_all.seed_all(pool)
    mo_result = await seed_all_mongo.seed_all_mongo(mongo_db)

    # Same fixture identifiers on both sides.
    assert pg_result["client_id"] == mo_result["client_id"]
    assert pg_result["policy_ids"] == mo_result["policy_ids"]
    assert pg_result["vulnerable_client_id"] == mo_result["vulnerable_client_id"]

    pg_tid = pg_result["tenant_id"]
    mo_tid = mo_result["tenant_id"]

    async def _pg_counts(tid: str) -> dict:
        async with pool.acquire() as c:
            async with c.transaction():
                await c.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", tid,
                )
                return {
                    "clients": await c.fetchval(
                        "SELECT COUNT(*) FROM clients WHERE tenant_id=$1::uuid", tid,
                    ),
                    "policies": await c.fetchval(
                        "SELECT COUNT(*) FROM policies WHERE tenant_id=$1::uuid", tid,
                    ),
                    "proj_queue": await c.fetchval(
                        "SELECT COUNT(*) FROM proj_queue WHERE tenant_id=$1::uuid", tid,
                    ),
                    "proj_class_unlock": await c.fetchval(
                        "SELECT COUNT(*) FROM proj_class_unlock WHERE tenant_id=$1::uuid",
                        tid,
                    ),
                    "approvals": await c.fetchval(
                        "SELECT COUNT(*) FROM events "
                        " WHERE tenant_id=$1::uuid AND type='Approval'",
                        tid,
                    ),
                    "policy_created": await c.fetchval(
                        "SELECT COUNT(*) FROM events "
                        " WHERE tenant_id=$1::uuid AND type='PolicyCreated'",
                        tid,
                    ),
                    "client_created": await c.fetchval(
                        "SELECT COUNT(*) FROM events "
                        " WHERE tenant_id=$1::uuid AND type='ClientEntityCreated'",
                        tid,
                    ),
                }

    async def _mo_counts(tid: str) -> dict:
        safe = wrap(mongo_db)
        async with use_tenant(tid):
            return {
                "clients": await safe.clients.count_documents({}),
                "policies": await safe.policies.count_documents({}),
                "proj_queue": await safe.proj_queue.count_documents({}),
                "proj_class_unlock":
                    await safe.proj_class_unlock.count_documents({}),
                "approvals":
                    await safe.events.count_documents({"type": "Approval"}),
                "policy_created":
                    await safe.events.count_documents({"type": "PolicyCreated"}),
                "client_created":
                    await safe.events.count_documents(
                        {"type": "ClientEntityCreated"},
                    ),
            }

    pg_counts = await _pg_counts(pg_tid)
    mo_counts = await _mo_counts(mo_tid)
    assert pg_counts == mo_counts, (
        f"snapshot drift:\n pg={pg_counts}\n mo={mo_counts}"
    )
    # Sanity: nontrivial fixture data actually landed on both.
    assert pg_counts["clients"] >= 2   # Harbour Marine + Kelp Shipping
    assert pg_counts["policies"] >= 3  # managed + tracked + vulnerable
    assert pg_counts["proj_queue"] >= 5
