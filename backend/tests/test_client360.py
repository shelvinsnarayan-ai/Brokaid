"""Phase 7 — /api/o/clients + /client/:id + /client/:id/timeline."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def _dev(client, email):
    r = await client.post("/api/auth/dev/impersonate", json={"email": email})
    assert r.status_code == 200, r.text


async def test_clients_list_and_360(app_client, seeded):
    app, client = app_client
    from app.seeds.seed_all_mongo import seed_all_mongo
    result = await seed_all_mongo(app.state.mongo_db)
    await _dev(client, "principal@burmac.example")

    r = await client.get("/api/o/clients")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["clients"]) >= 1
    cid = result["client_id"]

    r2 = await client.get(f"/api/o/client/{cid}")
    assert r2.status_code == 200
    hdr = r2.json()["client"]
    assert hdr["client_id"] == cid
    assert hdr["active_policies"] >= 1
    assert "jurisdiction_code" in hdr


async def test_client_timeline_paginates(app_client, seeded):
    app, client = app_client
    from app.seeds.seed_all_mongo import seed_all_mongo
    result = await seed_all_mongo(app.state.mongo_db)
    await _dev(client, "principal@burmac.example")
    cid = result["client_id"]

    r = await client.get(f"/api/o/client/{cid}/timeline?limit=5")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["client_id"] == cid
    assert body["limit"] == 5
    assert isinstance(body["events"], list)
