"""Phase 14 — Shadow Mode dashboard + export."""
from __future__ import annotations

import csv
import io
import uuid

import pytest

from app.kernel.trail import append as trail_append


pytestmark = pytest.mark.asyncio


DEV_PASSWORD = "dev-password-not-for-production"


async def _operator_login(client, email: str, pool):
    r = await client.post("/api/auth/dev/impersonate", json={"email": email})
    assert r.status_code == 200, r.text


async def _client_login(client, email: str):
    r = await client.post("/api/auth/dev/impersonate", json={"email": email})
    assert r.status_code == 200, r.text


async def _seed_unlock(pool, tenant_id, class_key, seen, agree, eno, unlocked=False):
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            await c.execute(
                """
                INSERT INTO proj_class_unlock
                  (tenant_id, class_key, decisions_seen, agreements,
                   eno_misses, unlocked)
                VALUES ($1::uuid, $2, $3, $4, $5, $6)
                ON CONFLICT (tenant_id, class_key) DO UPDATE SET
                  decisions_seen = EXCLUDED.decisions_seen,
                  agreements     = EXCLUDED.agreements,
                  eno_misses     = EXCLUDED.eno_misses,
                  unlocked       = EXCLUDED.unlocked
                """,
                tenant_id, class_key, seen, agree, eno, unlocked,
            )


async def test_summary_returns_at_least_10_rows_after_seed(
    app_client, scene_seeded, pool,
):
    _, client = app_client
    op = next(o for o in scene_seeded["operators"] if o["role"] == "principal")
    await _operator_login(client, op["email"], pool)
    r = await client.get("/api/o/shadow-mode/summary?window=all")
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    assert len(rows) >= 10, (
        f"expected ≥10 unique class rows post-seed; got {len(rows)}"
    )
    # No internal agent names ever surface — display strings only.
    for row in rows:
        assert row["agent_display"] and row["agent_display"][0].isupper()
        assert row["class_display"]
        assert "unlock_status" in row


async def test_agreement_rate_math(app_client, scene_seeded, pool):
    _, client = app_client
    tenant_id = scene_seeded["tenant_id"]
    # 20 paired decisions across 2 classes with known rates.
    # class A: 15 agreed / 20 = 0.75, class B: 5 agreed / 20 = 0.25.
    await _seed_unlock(pool, tenant_id, "request_info", 20, 15, 0)
    await _seed_unlock(pool, tenant_id, "submit_to_market", 20, 5, 0)

    op = next(o for o in scene_seeded["operators"] if o["role"] == "principal")
    await _operator_login(client, op["email"], pool)
    r = await client.get("/api/o/shadow-mode/summary?window=all")
    rows = r.json()["rows"]
    a = next(x for x in rows if x["class_key"] == "request_info")
    b = next(x for x in rows if x["class_key"] == "submit_to_market")
    assert a["decisions_seen"] == 20 and a["agreements"] == 15
    assert a["agreement_rate"] == 0.75
    assert b["decisions_seen"] == 20 and b["agreements"] == 5
    assert b["agreement_rate"] == 0.25


async def test_eno_flag_counted_correctly(app_client, scene_seeded, pool):
    _, client = app_client
    tenant_id = scene_seeded["tenant_id"]
    # 20 agreed decisions but 1 E&O miss and unlocked → relocked_recently.
    await _seed_unlock(
        pool, tenant_id, "structure_risk", 20, 20, 1, unlocked=True,
    )
    op = next(o for o in scene_seeded["operators"] if o["role"] == "principal")
    await _operator_login(client, op["email"], pool)
    r = await client.get("/api/o/shadow-mode/summary?window=all")
    row = next(
        x for x in r.json()["rows"] if x["class_key"] == "structure_risk"
    )
    assert row["eno_misses"] == 1
    assert row["unlock_status"] == "relocked_recently"


async def test_export_csv_shape(app_client, scene_seeded, pool):
    _, client = app_client
    op = next(o for o in scene_seeded["operators"] if o["role"] == "principal")
    await _operator_login(client, op["email"], pool)
    r_sum = await client.get("/api/o/shadow-mode/summary?window=all")
    rows = r_sum.json()["rows"]

    r_csv = await client.get("/api/o/shadow-mode/export?format=csv&window=all")
    assert r_csv.status_code == 200
    assert r_csv.headers["content-type"].startswith("text/csv")
    text = r_csv.text
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    assert header == [
        "agent_name", "class_key", "decisions_seen", "agreements",
        "agreement_rate", "eno_misses", "unlocked", "unlock_status",
        "last_decision_at",
    ]
    body_rows = list(reader)
    assert len(body_rows) == len(rows)


async def test_export_json_shape(app_client, scene_seeded, pool):
    _, client = app_client
    op = next(o for o in scene_seeded["operators"] if o["role"] == "principal")
    await _operator_login(client, op["email"], pool)
    r = await client.get("/api/o/shadow-mode/export?format=json&window=all")
    assert r.status_code == 200
    data = r.json()
    assert "rows" in data
    assert "window" in data
    for row in data["rows"]:
        assert "agent_name" in row and "class_key" in row
        assert "agreement_rate" in row


async def test_drill_returns_paired_records(app_client, scene_seeded, pool):
    _, client = app_client
    tenant_id = scene_seeded["tenant_id"]
    op = next(o for o in scene_seeded["operators"] if o["role"] == "principal")
    await _operator_login(client, op["email"], pool)
    # Emit a matched pair explicitly so drill has content.
    env_id = str(uuid.uuid4())
    await trail_append(
        pool, tenant_id, "AgentOutputProduced",
        actor={"kind": "system", "id": "agent", "name": "agent"},
        payload={
            "envelope_id": env_id, "class_key": "request_info",
            "output": {"summary": "Test proposal", "fields": {}},
            "confidence": {"scalar": 0.9},
            "citations": [], "tokens_used": {"input": 0, "output": 0},
        },
        idempotency_key=f"drill-test-out:{env_id}",
    )
    await trail_append(
        pool, tenant_id, "Approval",
        actor={"kind": "human", "id": "principal", "name": "principal"},
        payload={
            "envelope_id": env_id, "class_key": "request_info",
            "user_id": "principal", "role": "principal",
            "decision": "approve", "edits": {}, "reason": None,
        },
        idempotency_key=f"drill-test-appr:{env_id}",
    )
    r = await client.get(
        "/api/o/shadow-mode/decisions?agent=intake&class=request_info&window=all"
    )
    assert r.status_code == 200, r.text
    pairs = r.json()["pairs"]
    assert len(pairs) >= 1
    hit = next(p for p in pairs if p["envelope_id"] == env_id)
    assert hit["human_decision"] == "approve"
    assert hit["agreed"] is True
    assert hit["event_refs"]["agent_output_event_id"] is not None
    assert hit["event_refs"]["approval_event_id"] is not None
    assert hit["proposal_summary"] == "Test proposal"


async def test_shadow_mode_requires_operator_role(app_client, scene_seeded, pool):
    _, client = app_client
    client_owner = next(
        c for c in scene_seeded["clients"] if c["role"] == "client_owner"
    )
    await _client_login(client, client_owner["email"])
    r = await client.get("/api/o/shadow-mode/summary")
    assert r.status_code in (401, 403), f"got {r.status_code}: {r.text}"
