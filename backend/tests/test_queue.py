"""Phase 7 — /api/o/queue shape + sort + decision card."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def _op_session(client, email: str = "principal@harbourline.example"):
    r = await client.post(
        "/api/auth/dev/impersonate", json={"email": email},
    )
    assert r.status_code == 200, r.text


async def test_queue_lists_seeded_cards_sorted(app_client, seeded):
    app, client = app_client
    from app.seeds.seed_all_mongo import seed_all_mongo
    await seed_all_mongo(app.state.mongo_db)
    await _op_session(client)

    r = await client.get("/api/o/queue")
    assert r.status_code == 200, r.text
    body = r.json()
    cards = body["cards"]
    assert body["count"] == len(cards) >= 5  # R1.3: count follows the seed, not a hard-coded 5
    rank = {"overdue": 0, "warn": 1, "ok": 2}
    assert [rank.get(c["sla_state"], 3) for c in cards] == sorted(rank.get(c["sla_state"], 3) for c in cards)
    # Overdue first; the two ~5h aged cards have SLA_MINUTES=240 for
    # certificate_issue and structure_risk → both overdue. Also the
    # claim_admin 7h card (SLA 480 min = 8h) is warn/ok, not overdue.
    states = [c["sla_state"] for c in cards]
    # We expect at least 2 overdue in the head of the list.
    assert states.count("overdue") >= 2
    # First one must be overdue.
    assert states[0] == "overdue"


async def test_decision_card_shape(app_client, seeded):
    app, client = app_client
    from app.seeds.seed_all_mongo import seed_all_mongo
    await seed_all_mongo(app.state.mongo_db)
    await _op_session(client)

    r = await client.get("/api/o/queue")
    envelope_id = r.json()["cards"][0]["envelope_id"]
    r2 = await client.get(f"/api/o/queue/{envelope_id}")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert set(body.keys()) >= {"card", "proposal", "evidence", "confidence"}
    conf = body["confidence"]
    assert conf["source"] == "computed"
    assert set(conf["components"].keys()) == {
        "schema_validity", "completeness",
        "citation_existence", "class_agreement",
    }
    assert conf["scalar"] is not None
