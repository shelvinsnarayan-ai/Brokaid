"""Phase R5c-1b-β — shadow_mode kernel extract-first pins.

Locks observable behaviour of `kernel/shadow_mode.py` on both
backends after the extraction from `routes/o_shadow_mode.py`. PG
path is the byte-verbatim pre-extraction inline SQL; Mongo path
uses `TenantSafeDatabase` under `use_tenant(tid)`.

Coverage
--------
- `parse_window`
    * '7d' / '30d' / 'all' map to a datetime | None.
    * Any other string raises ValueError.
- `summary_rows`
    * PG returns one row per class in proj_class_unlock, with
      first/last decision timestamps computed from Approval events.
    * Rows with empty `class_key` are filtered out.
    * Mongo returns the same list under `use_tenant`.
- `paired_decisions`
    * PG pairs an Approval with the earliest AgentOutputProduced
      sharing the same envelope_id; unpaired Approvals still surface
      with output_payload=None.
    * Mongo returns the same pair shape on identical seeds.
    * Window filter is honoured on both backends (older Approvals
      excluded when window='7d').
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.tenant_safe import wrap as _wrap_safe
from app.kernel import shadow_mode, trail, trail_mongo
from app.kernel.tenant_ctx import use_tenant


pytestmark = pytest.mark.asyncio


# ---- fixtures & helpers ---------------------------------------------

async def _pg_tenant(pool) -> str:
    slug = f"sm-{uuid.uuid4().hex[:6]}"
    t, _ = await trail.create_tenant(pool, slug=slug, display_name=slug)
    return t.tenant_id


async def _matched_tenant(pool, mongo_db) -> str:
    slug = f"sm-{uuid.uuid4().hex[:6]}"
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


async def _seed_pg_unlock(
    pool, tid: str, class_key: str,
    seen: int, agree: int, eno: int, unlocked: bool = False,
) -> None:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tid,
            )
            await c.execute(
                "INSERT INTO proj_class_unlock "
                "  (tenant_id, class_key, decisions_seen, agreements, "
                "   eno_misses, unlocked) "
                "VALUES ($1::uuid, $2, $3, $4, $5, $6)",
                tid, class_key, seen, agree, eno, unlocked,
            )


async def _seed_mongo_unlock(
    mongo_db, tid: str, class_key: str,
    seen: int, agree: int, eno: int, unlocked: bool = False,
) -> None:
    safe = _wrap_safe(mongo_db)
    async with use_tenant(tid):
        await safe.proj_class_unlock.insert_one({
            "class_key": class_key,
            "decisions_seen": seen,
            "agreements": agree,
            "eno_misses": eno,
            "unlocked": unlocked,
        })


async def _pg_append(pool, tid: str, typ: str, payload: dict,
                     idem: str, ts_override: datetime | None = None) -> None:
    await trail.append(
        pool, tid, typ,
        actor={"kind": "system", "id": "pin", "name": "pin"},
        payload=payload, idempotency_key=idem, ts_override=ts_override,
    )


async def _mongo_append(mongo_db, tid: str, typ: str, payload: dict,
                        idem: str, ts_override: datetime | None = None) -> None:
    async with use_tenant(tid):
        await trail_mongo.append_event(
            mongo_db, tid, typ,
            actor={"kind": "system", "id": "pin", "name": "pin"},
            payload=payload, idempotency_key=idem, ts_override=ts_override,
        )


# ---- Pin 1: parse_window ---------------------------------------------
async def test_pin_parse_window_recognised_values():
    assert shadow_mode.parse_window("all") is None
    seven = shadow_mode.parse_window("7d")
    thirty = shadow_mode.parse_window("30d")
    assert seven is not None and thirty is not None
    now = datetime.now(timezone.utc)
    assert (now - seven) >= timedelta(days=7) - timedelta(seconds=5)
    assert (now - thirty) >= timedelta(days=30) - timedelta(seconds=5)


async def test_pin_parse_window_rejects_invalid():
    with pytest.raises(ValueError):
        shadow_mode.parse_window("14d")
    with pytest.raises(ValueError):
        shadow_mode.parse_window("")


# ---- Pin 2: PG summary_rows -----------------------------------------
async def test_pin_pg_summary_rows_shape(pool):
    tid = await _pg_tenant(pool)
    await _seed_pg_unlock(pool, tid, "request_info", 10, 9, 0)
    await _seed_pg_unlock(pool, tid, "submit_to_market", 5, 4, 1, unlocked=True)
    # Approval events driving first/last timestamps.
    ts1 = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    ts2 = datetime(2025, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    await _pg_append(
        pool, tid, "Approval",
        {"class_key": "request_info", "decision": "approve"},
        idem=f"pin:sm:ap1:{tid}", ts_override=ts1,
    )
    await _pg_append(
        pool, tid, "Approval",
        {"class_key": "request_info", "decision": "edit", "edits": {"x": 1}},
        idem=f"pin:sm:ap2:{tid}", ts_override=ts2,
    )

    rows = await shadow_mode.summary_rows(pool, tid, "all")
    by_class = {r["class_key"]: r for r in rows}
    assert set(by_class.keys()) == {"request_info", "submit_to_market"}
    ri = by_class["request_info"]
    assert ri["decisions_seen"] == 10
    assert ri["agreements"] == 9
    assert ri["agreement_rate"] == round(9 / 10, 4)
    assert ri["eno_misses"] == 0
    assert ri["unlocked"] is False
    assert ri["first_decision_at"].startswith("2025-06-01T12:00:00")
    assert ri["last_decision_at"].startswith("2025-06-05T12:00:00")

    stm = by_class["submit_to_market"]
    assert stm["unlocked"] is True
    assert stm["first_decision_at"] is None
    assert stm["last_decision_at"] is None


# ---- Pin 3: Mongo summary_rows --------------------------------------
async def test_pin_mongo_summary_rows_shape(pool, mongo_db):
    tid = await _matched_tenant(pool, mongo_db)
    await _seed_mongo_unlock(mongo_db, tid, "request_info", 10, 9, 0)
    await _seed_mongo_unlock(
        mongo_db, tid, "submit_to_market", 5, 4, 1, unlocked=True,
    )
    ts1 = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    ts2 = datetime(2025, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
    await _mongo_append(
        mongo_db, tid, "Approval",
        {"class_key": "request_info", "decision": "approve"},
        idem=f"pin:msm:ap1:{tid}", ts_override=ts1,
    )
    await _mongo_append(
        mongo_db, tid, "Approval",
        {"class_key": "request_info", "decision": "edit", "edits": {"x": 1}},
        idem=f"pin:msm:ap2:{tid}", ts_override=ts2,
    )

    rows = await shadow_mode.summary_rows(mongo_db, tid, "all")
    by_class = {r["class_key"]: r for r in rows}
    assert set(by_class.keys()) == {"request_info", "submit_to_market"}
    ri = by_class["request_info"]
    assert ri["decisions_seen"] == 10
    assert ri["agreements"] == 9
    assert ri["agreement_rate"] == round(9 / 10, 4)
    assert ri["first_decision_at"].startswith("2025-06-01T12:00:00")
    assert ri["last_decision_at"].startswith("2025-06-05T12:00:00")
    assert by_class["submit_to_market"]["unlocked"] is True


# ---- Pin 4: PG paired_decisions -------------------------------------
async def test_pin_pg_paired_decisions_matches_by_envelope(pool):
    tid = await _pg_tenant(pool)
    env_a = str(uuid.uuid4())
    env_b = str(uuid.uuid4())
    class_key = "structure_risk"

    # Output events for env_a (paired) and one unrelated env.
    await _pg_append(
        pool, tid, "AgentOutputProduced",
        {"envelope_id": env_a, "class_key": class_key,
         "output": {"summary": "SR proposal A"}},
        idem=f"pin:pd:out_a:{tid}",
    )
    # Approvals: env_a (paired), env_b (unpaired).
    await _pg_append(
        pool, tid, "Approval",
        {"envelope_id": env_a, "class_key": class_key,
         "decision": "approve"},
        idem=f"pin:pd:app_a:{tid}",
    )
    await _pg_append(
        pool, tid, "Approval",
        {"envelope_id": env_b, "class_key": class_key,
         "decision": "reject", "eno_flag": True},
        idem=f"pin:pd:app_b:{tid}",
    )

    pairs = await shadow_mode.paired_decisions(
        pool, tid, class_key, "all", limit=50,
    )
    assert len(pairs) == 2
    by_env = {p["envelope_id"]: p for p in pairs}
    p_a = by_env[env_a]
    assert p_a["output_payload"] is not None
    assert p_a["output_payload"]["output"]["summary"] == "SR proposal A"
    assert p_a["approval_payload"]["decision"] == "approve"
    p_b = by_env[env_b]
    assert p_b["output_payload"] is None
    assert p_b["approval_payload"]["decision"] == "reject"


# ---- Pin 5: Mongo paired_decisions ----------------------------------
async def test_pin_mongo_paired_decisions_matches_by_envelope(pool, mongo_db):
    tid = await _matched_tenant(pool, mongo_db)
    env_a = str(uuid.uuid4())
    env_b = str(uuid.uuid4())
    class_key = "structure_risk"

    await _mongo_append(
        mongo_db, tid, "AgentOutputProduced",
        {"envelope_id": env_a, "class_key": class_key,
         "output": {"summary": "SR proposal A"}},
        idem=f"pin:mpd:out_a:{tid}",
    )
    await _mongo_append(
        mongo_db, tid, "Approval",
        {"envelope_id": env_a, "class_key": class_key,
         "decision": "approve"},
        idem=f"pin:mpd:app_a:{tid}",
    )
    await _mongo_append(
        mongo_db, tid, "Approval",
        {"envelope_id": env_b, "class_key": class_key,
         "decision": "reject", "eno_flag": True},
        idem=f"pin:mpd:app_b:{tid}",
    )

    pairs = await shadow_mode.paired_decisions(
        mongo_db, tid, class_key, "all", limit=50,
    )
    assert len(pairs) == 2
    by_env = {p["envelope_id"]: p for p in pairs}
    p_a = by_env[env_a]
    assert p_a["output_payload"] is not None
    assert p_a["output_payload"]["output"]["summary"] == "SR proposal A"
    assert p_a["approval_payload"]["decision"] == "approve"
    p_b = by_env[env_b]
    assert p_b["output_payload"] is None
    assert p_b["approval_payload"]["decision"] == "reject"


# ---- Pin 6: PG window filter excludes older Approvals ---------------
async def test_pin_pg_paired_decisions_window_excludes_old(pool):
    tid = await _pg_tenant(pool)
    class_key = "endorsement_standard"
    old_env = str(uuid.uuid4())
    new_env = str(uuid.uuid4())
    old_ts = datetime.now(timezone.utc) - timedelta(days=90)
    new_ts = datetime.now(timezone.utc) - timedelta(days=1)

    await _pg_append(
        pool, tid, "Approval",
        {"envelope_id": old_env, "class_key": class_key, "decision": "approve"},
        idem=f"pin:pwd:old:{tid}", ts_override=old_ts,
    )
    await _pg_append(
        pool, tid, "Approval",
        {"envelope_id": new_env, "class_key": class_key, "decision": "approve"},
        idem=f"pin:pwd:new:{tid}", ts_override=new_ts,
    )

    all_pairs = await shadow_mode.paired_decisions(
        pool, tid, class_key, "all", limit=50,
    )
    recent_pairs = await shadow_mode.paired_decisions(
        pool, tid, class_key, "7d", limit=50,
    )
    assert {p["envelope_id"] for p in all_pairs} == {old_env, new_env}
    assert {p["envelope_id"] for p in recent_pairs} == {new_env}


# ---- Pin 7: Mongo window filter excludes older Approvals ------------
async def test_pin_mongo_paired_decisions_window_excludes_old(pool, mongo_db):
    tid = await _matched_tenant(pool, mongo_db)
    class_key = "endorsement_standard"
    old_env = str(uuid.uuid4())
    new_env = str(uuid.uuid4())
    old_ts = datetime.now(timezone.utc) - timedelta(days=90)
    new_ts = datetime.now(timezone.utc) - timedelta(days=1)

    await _mongo_append(
        mongo_db, tid, "Approval",
        {"envelope_id": old_env, "class_key": class_key, "decision": "approve"},
        idem=f"pin:mwd:old:{tid}", ts_override=old_ts,
    )
    await _mongo_append(
        mongo_db, tid, "Approval",
        {"envelope_id": new_env, "class_key": class_key, "decision": "approve"},
        idem=f"pin:mwd:new:{tid}", ts_override=new_ts,
    )

    all_pairs = await shadow_mode.paired_decisions(
        mongo_db, tid, class_key, "all", limit=50,
    )
    recent_pairs = await shadow_mode.paired_decisions(
        mongo_db, tid, class_key, "7d", limit=50,
    )
    assert {p["envelope_id"] for p in all_pairs} == {old_env, new_env}
    assert {p["envelope_id"] for p in recent_pairs} == {new_env}
