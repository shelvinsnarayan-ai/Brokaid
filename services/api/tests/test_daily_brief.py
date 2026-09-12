"""Phase 7 — /api/o/daily_brief shape (projection-only, no narrative)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def _dev(client, email):
    r = await client.post("/api/auth/dev/impersonate", json={"email": email})
    assert r.status_code == 200, r.text


async def test_daily_brief_has_numeric_shape(app_client, seeded):
    app, client = app_client
    from app.seeds.seed_all import seed_all
    await seed_all(app.state.pool)
    await _dev(client, "principal@burmac.example")

    r = await client.get("/api/o/daily_brief")
    assert r.status_code == 200, r.text
    body = r.json()
    required = {
        "date", "tenant", "new_matters", "endorsements_in_flight",
        "claims_in_flight", "hitl_queue_size", "overdue_hitl",
        "kpis_of_note",
    }
    assert set(body.keys()) >= required
    # No narrative field of any kind.
    assert "narrative" not in body
    assert "summary_sentence" not in body
    assert isinstance(body["kpis_of_note"], list)
    assert isinstance(body["hitl_queue_size"], int)
    assert body["hitl_queue_size"] >= 5   # 5 seeded pending cards
