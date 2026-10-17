"""Phase 11 — vulnerable-flag beat.

A vulnerable client's outbound SMS is suppressed and routed to the
operator queue instead. Zero mock-channel invocations must fire.
"""
from __future__ import annotations

import uuid


from app.kernel.mocks import sms as sms_mock


async def test_vulnerable_flag_beat_suppresses_sms(app_client, scene_seeded) -> None:
    app, _client = app_client
    pool = app.state.pool
    tenant_id = scene_seeded["tenant_id"]
    vuln_client_id = scene_seeded["vulnerable_client_id"]

    sms_mock.reset_counters()
    assert sms_mock.SENT_SMS_COUNT == 0

    envelope_id = str(uuid.uuid4())
    result = await sms_mock.send(
        pool=pool,
        tenant_id=tenant_id,
        envelope_id=envelope_id,
        client_id=vuln_client_id,
        matter_id=None,
        message="Confirming your endorsement has been placed.",
    )

    assert result["status"] == "suppressed_for_operator_review"
    assert result["reason"] == "vulnerable_flag"
    assert result["mocked"] is True

    # NO SMS was sent through the mock channel.
    assert sms_mock.SENT_SMS_COUNT == 0

    # SmsSuppressedForOperatorReview event on the ledger.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            sup = await c.fetchrow(
                "SELECT payload, mocked FROM events "
                " WHERE tenant_id=$1::uuid AND type='SmsSuppressedForOperatorReview' "
                "   AND payload->>'envelope_id' = $2",
                tenant_id, envelope_id,
            )
            queued = await c.fetchrow(
                "SELECT class_key, gate_reason FROM proj_queue "
                " WHERE tenant_id=$1::uuid AND envelope_id=$2::uuid",
                tenant_id, envelope_id,
            )
    assert sup is not None, "expected SmsSuppressedForOperatorReview event"
    assert sup["mocked"] is True
    assert queued is not None, "expected queue card for the suppressed SMS"
    assert queued["class_key"] == "comms_service"
    assert queued["gate_reason"] == "vulnerable_flag_set"


async def test_non_vulnerable_sms_release_path(app_client, scene_seeded, monkeypatch) -> None:
    """A non-vulnerable client sends through the release pipeline.

    Uses a monkeypatched compliance classifier so this test doesn't hit
    the LLM (belt-and-braces: the release path is exercised via unit
    tests already; this one just proves the vulnerable check gates SMS
    at the top of the stack).
    """
    from app.kernel import release as release_module

    async def _stub_classifier(*, draft, context, author_agent):   # noqa: ARG001
        return {
            "advice_classification": "factual",
            "classification_rationale": "Neutral update.",
            "risks_flagged": [],
            "recommended_rewrite": None,
            "release_verdict": "release",
            "_tokens_used": {"input": 30, "output": 10},
            "_wall_ms": 50,
        }

    monkeypatch.setattr(release_module, "_default_classifier", _stub_classifier)

    app, _ = app_client
    pool = app.state.pool
    tenant_id = scene_seeded["tenant_id"]
    non_vuln_client_id = scene_seeded["client_id"]      # Harbour Marine — not flagged

    sms_mock.reset_counters()
    envelope_id = str(uuid.uuid4())
    result = await sms_mock.send(
        pool=pool,
        tenant_id=tenant_id,
        envelope_id=envelope_id,
        client_id=non_vuln_client_id,
        matter_id=None,
        message="Your certificate has been regenerated. Standard update, no action needed.",
    )
    assert result["status"] == "sent"
    assert sms_mock.SENT_SMS_COUNT == 1
