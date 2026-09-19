"""Phase 5 — jurisdiction packs + preview mechanic (R5c-4b — Mongo)."""
from __future__ import annotations

import pytest

from app.seeds.seed_all_mongo import seed_all_mongo

pytestmark = pytest.mark.asyncio


async def test_jurisdictions_list_returns_au_and_sg(app_client):
    _, client = app_client
    r = await client.get("/api/jurisdictions")
    assert r.status_code == 200
    codes = sorted(p["code"] for p in r.json()["jurisdictions"])
    assert codes == ["AU", "SG"]


async def test_au_pack_shape(app_client):
    _, client = app_client
    r = await client.get("/api/jurisdictions/AU")
    b = r.json()
    assert b["currency_code"] == "AUD"
    assert b["rail_label"] == "PayTo"
    assert b["locale"] == "en-AU"
    assert b["regulator"] == "ASIC"


async def test_sg_pack_shape(app_client):
    _, client = app_client
    r = await client.get("/api/jurisdictions/SG")
    b = r.json()
    assert b["currency_code"] == "SGD"
    assert b["rail_label"] == "PayNow"
    assert b["locale"] == "en-SG"
    assert b["regulator"] == "MAS"


async def test_every_disclosure_string_is_marked_placeholder(app_client):
    _, client = app_client
    for code in ("AU", "SG"):
        r = await client.get(f"/api/jurisdictions/{code}")
        for name, text in r.json()["disclosure_strings"].items():
            assert "<FICTIONAL_PLACEHOLDER>" in text, f"{code}.{name} missing marker"


async def test_unknown_jurisdiction_404(app_client):
    _, client = app_client
    r = await client.get("/api/jurisdictions/US")
    assert r.status_code == 404


async def test_client_preview_flips_currency_and_flags_preview_mode(app_client):
    app, client = app_client
    await seed_all_mongo(app.state.mongo_db)

    r = await client.post(
        "/api/auth/dev/impersonate",
        json={"email": "owner@fictionalclient.example"},
    )
    assert r.status_code == 200

    r = await client.get("/api/c/policies")
    assert r.json()["jurisdiction"]["currency_code"] == "AUD"
    assert r.json()["preview_mode"] is False

    r = await client.post("/api/c/preferences", json={"jurisdiction": "SG"})
    assert r.status_code == 200
    r = await client.get("/api/c/policies")
    b = r.json()
    assert b["jurisdiction"]["currency_code"] == "SGD"
    assert b["jurisdiction"]["rail_label"] == "PayNow"
    assert b["preview_mode"] is True
    assert b["actual_jurisdiction"] == "AU"
    for p in b["policies"]:
        assert p["premium"]["currency"] == "SGD"

    r = await client.post("/api/c/preferences", json={"jurisdiction": None})
    assert r.status_code == 200
    r = await client.get("/api/c/policies")
    assert r.json()["jurisdiction"]["currency_code"] == "AUD"
    assert r.json()["preview_mode"] is False
