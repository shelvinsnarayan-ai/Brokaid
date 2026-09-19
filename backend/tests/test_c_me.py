"""Phase 6 — /api/c/me hydration endpoint (R5c-4b — Mongo)."""
from __future__ import annotations

import pytest

from app.seeds.seed_all_mongo import seed_all_mongo

pytestmark = pytest.mark.asyncio


async def test_c_me_unauthenticated_401(app_client):
    _, client = app_client
    r = await client.get("/api/c/me")
    assert r.status_code == 401


async def test_c_me_returns_user_client_and_prefs(app_client):
    app, client = app_client
    await seed_all_mongo(app.state.mongo_db)

    r = await client.post(
        "/api/auth/dev/impersonate",
        json={"email": "owner@fictionalclient.example"},
    )
    assert r.status_code == 200

    r = await client.get("/api/c/me")
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["email"] == "owner@fictionalclient.example"
    assert body["user"]["kind"] == "client"
    assert body["linked_client"]["display_name"] == "Harbour Marine Services Pty Ltd"
    assert body["linked_client"]["jurisdiction_code"] == "AU"
    assert body["client_prefs"] == {}

    # Set a preview and re-hydrate.
    r = await client.post("/api/c/preferences", json={"jurisdiction": "SG"})
    assert r.status_code == 200
    r = await client.get("/api/c/me")
    assert r.status_code == 200
    assert r.json()["client_prefs"]["jurisdiction"] == "SG"
