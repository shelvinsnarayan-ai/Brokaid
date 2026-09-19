"""Phase R5b-2b — Ask channel end-to-end under `BROKAID_LEDGER=mongo`.

Reuses the `scene6_ctx` fixture from `test_r5b2_scene6_claim_e2e_mongo`
(which itself extends `mongo_e2e` from `test_queue_e2e_mongo`). Adds:

  - liAIson monkey-patch: `EmergentLLMProviderLiaison.draft` returns
    a deterministic dict so the endpoint runs without EMERGENT_LLM_KEY
    (phase-0 zero-LLM-budget discipline preserved).
  - Compliance classifier monkey-patch (on `release._default_classifier`)
    for the release-path test.

Three acceptance tests:
  1. Benign question + stub classifier → verdict='release',
     ClientQuestionAsked + ClientMessageReleased both land in Mongo.
  2. Coverage-guard-tripping draft from liAIson → verdict=
     'coverage_guard_block', ClientQuestionAsked lands but NO release
     event is emitted (classifier is never called — deterministic
     guard short-circuits).
  3. RBAC negative: an operator session cannot POST /api/c/ask/message
     (operator has no user_client_link → 404 or 403).
"""
from __future__ import annotations

import pytest

from app.auth.sessions import COOKIE_NAME

pytestmark = pytest.mark.asyncio

pytest_plugins = ["tests.test_r5b2_scene6_claim_e2e_mongo"]


def _cookie(sid: str) -> dict[str, str]:
    return {COOKIE_NAME: sid}


async def _stub_liaison_draft_factory(response_text: str):
    async def _stub_draft(self, *, client_message, context):
        return {
            "response_draft": response_text,
            "broker_attribution": "Your broker is across this.",
            "next_step": "We'll be in touch shortly.",
            "detected_coverage_question": False,
            "citations": [],
            "_tokens_used": {"input": 0, "output": 0},
            "_wall_ms": 0,
        }
    return _stub_draft


# ---- 1. Ask release path ---------------------------------------------
async def test_r5b2b_ask_release_writes_client_message_released_on_mongo(
    scene6_ctx, monkeypatch,
):
    from app.db.tenant_safe import wrap
    from app.kernel import release as release_mod
    from app.kernel.agents import liaison as liaison_mod
    from app.kernel.tenant_ctx import use_tenant

    stub = await _stub_liaison_draft_factory(
        "Your policy renewal is booked for 2 January. We'll be in touch.",
    )
    monkeypatch.setattr(
        liaison_mod.EmergentLLMProviderLiaison, "draft", stub,
    )

    async def _stub_classifier(*, draft, context, author_agent):
        return {
            "advice_classification": "factual",
            "classification_rationale": "test-stub — deterministic release",
            "risks_flagged": [],
            "recommended_rewrite": None,
            "release_verdict": "release",
            "_tokens_used": {"input": 0, "output": 0},
            "_wall_ms": 0,
        }
    monkeypatch.setattr(release_mod, "_default_classifier", _stub_classifier)

    r = await scene6_ctx["client"].post(
        "/api/c/ask/message",
        json={"message": "When is my policy up for renewal?"},
        cookies=_cookie(scene6_ctx["client_session_id"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["released"] is True
    assert body["verdict"] == "release"
    envelope_id = body["envelope_id"]

    safe = wrap(scene6_ctx["mongo"])
    async with use_tenant(scene6_ctx["tenant_a"]):
        q = await safe.events.find_one({
            "type": "ClientQuestionAsked",
            "payload.envelope_id": envelope_id,
        })
        assert q is not None
        assert q["payload"]["client_id"] == scene6_ctx["client_id"]
        assert q["idempotency_key"] == f"ask:in:{envelope_id}"

        rel = await safe.events.find_one({
            "type": "ClientMessageReleased",
            "payload.envelope_id": envelope_id,
        })
        assert rel is not None
        assert rel["payload"]["author_agent"] == "liaison"
        assert rel["payload"]["classification"] == "factual"


# ---- 2. Ask coverage-guard-block short-circuit -----------------------
async def test_r5b2b_ask_coverage_guard_block_no_release_event(
    scene6_ctx, monkeypatch,
):
    from app.db.tenant_safe import wrap
    from app.kernel.agents import liaison as liaison_mod
    from app.kernel.tenant_ctx import use_tenant

    # A draft that trips the deterministic coverage-language guard.
    stub = await _stub_liaison_draft_factory(
        "You're covered for this. No question, we'll pay it.",
    )
    monkeypatch.setattr(
        liaison_mod.EmergentLLMProviderLiaison, "draft", stub,
    )

    safe = wrap(scene6_ctx["mongo"])
    async with use_tenant(scene6_ctx["tenant_a"]):
        n_rel_before = 0
        async for _ in safe.events.find({
            "type": {"$in": [
                "ClientMessageReleased",
                "ClientMessageRewriteRequired",
                "ClientMessageHITL",
            ]},
        }):
            n_rel_before += 1

    r = await scene6_ctx["client"].post(
        "/api/c/ask/message",
        json={"message": "Am I covered for storm damage?"},
        cookies=_cookie(scene6_ctx["client_session_id"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["released"] is False
    assert body["verdict"] == "coverage_guard_block"
    envelope_id = body["envelope_id"]

    async with use_tenant(scene6_ctx["tenant_a"]):
        # ClientQuestionAsked landed (fires before the release pipeline).
        q = await safe.events.find_one({
            "type": "ClientQuestionAsked",
            "payload.envelope_id": envelope_id,
        })
        assert q is not None

        # No release event landed.
        n_rel_after = 0
        async for _ in safe.events.find({
            "type": {"$in": [
                "ClientMessageReleased",
                "ClientMessageRewriteRequired",
                "ClientMessageHITL",
            ]},
        }):
            n_rel_after += 1
    assert n_rel_after == n_rel_before, (
        f"coverage_guard_block leaked a release event: "
        f"before={n_rel_before} after={n_rel_after}"
    )


# ---- 3. RBAC negative: operator session cannot use Ask ---------------
async def test_r5b2b_ask_operator_session_rejected(scene6_ctx, monkeypatch):
    """The operator session has no `user_client_link`, so
    `load_client_for_user` returns None → 404. Confirms tenant-scoped
    client resolution guards the ask channel."""
    from app.kernel.agents import liaison as liaison_mod

    stub = await _stub_liaison_draft_factory("test")
    monkeypatch.setattr(
        liaison_mod.EmergentLLMProviderLiaison, "draft", stub,
    )

    r = await scene6_ctx["client"].post(
        "/api/c/ask/message",
        json={"message": "hello"},
        cookies=_cookie(scene6_ctx["session_op_a"]),
    )
    # Session's user_kind='operator' → require_session rejects it as
    # 403, OR (if it lets through) load_client_for_user returns None
    # → 404. Either is a valid guard — 200 would be a P0 leak.
    assert r.status_code in (403, 404), (
        f"operator session on /api/c/ask returned {r.status_code} — "
        f"200 would be a P0 leak. body={r.text}"
    )
