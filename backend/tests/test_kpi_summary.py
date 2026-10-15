"""Phase 7 — /api/o/kpi/summary shape and always-render invariant."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def _op_session(client, seeded):
    r = await client.post(
        "/api/auth/dev/impersonate",
        json={"email": "principal@harbourline.example"},
    )
    assert r.status_code == 200, r.text


async def test_summary_returns_12_ordered(app_client, seeded):
    app, client = app_client
    from app.seeds.seed_all_mongo import seed_all_mongo
    await seed_all_mongo(app.state.mongo_db)

    await _op_session(client, seeded)
    r = await client.get("/api/o/kpi/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) >= {"day", "kpis"}
    assert len(body["kpis"]) == 12
    ids = [k["kpi_id"] for k in body["kpis"]]
    assert ids == list(range(1, 13))
    for k in body["kpis"]:
        assert set(k.keys()) >= {"kpi_id", "scalar", "meta",
                                 "delta_prev", "spark_series"}
        assert isinstance(k["spark_series"], list)
        assert len(k["spark_series"]) >= 1


async def test_not_yet_measurable_reasons(app_client, seeded):
    app, client = app_client
    from app.seeds.seed_all_mongo import seed_all_mongo
    await seed_all_mongo(app.state.mongo_db)

    await _op_session(client, seeded)
    r = await client.get("/api/o/kpi/summary")
    body = r.json()
    by_id = {k["kpi_id"]: k for k in body["kpis"]}
    # KPIs 3, 4, 6, 7, 12 are not yet measurable in phase 7.
    for kid in (3, 4, 6, 7, 12):
        assert by_id[kid]["meta"].get("not_yet_measurable") is True, kid
        assert by_id[kid]["meta"].get("reason"), kid
    # KPI 1 (Book) has managed policies in seed → measurable.
    assert by_id[1]["scalar"] > 0
