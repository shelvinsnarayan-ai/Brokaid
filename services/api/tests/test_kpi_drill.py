"""Phase 7 — /api/o/kpi/:id/events drill."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def _op_session(client):
    r = await client.post(
        "/api/auth/dev/impersonate",
        json={"email": "principal@burmac.example"},
    )
    assert r.status_code == 200, r.text


async def test_drillable_kpis_return_events(app_client, seeded):
    app, client = app_client
    from app.seeds.seed_all import seed_all
    await seed_all(app.state.pool)
    await _op_session(client)

    for kid in (5, 8, 10, 11):
        r = await client.get(f"/api/o/kpi/{kid}/events")
        assert r.status_code == 200, (kid, r.text)
        body = r.json()
        assert body["kpi_id"] == kid
        assert isinstance(body["events"], list)


async def test_non_drillable_kpis_reject(app_client, seeded):
    app, client = app_client
    from app.seeds.seed_all import seed_all
    await seed_all(app.state.pool)
    await _op_session(client)

    for kid in (1, 2, 3, 4, 6, 7, 9, 12):
        r = await client.get(f"/api/o/kpi/{kid}/events")
        assert r.status_code == 400, (kid, r.text)
        assert r.json()["detail"] == "not_drillable_in_this_build"
