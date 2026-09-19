"""Phase 3 contracts — round-trip and pair-validation tests."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.kernel.contracts import (
    Actor,
    Approval,
    Citation,
    ContextEnvelope,
    EventRef,
    InvocationBudget,
    OutputContract,
    TokenCount,
    validate_output_matches_envelope,
)


def _base_envelope(**overrides) -> ContextEnvelope:
    kwargs = dict(
        envelope_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        matter_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        class_key="structure_risk",
        intent="triage new matter",
        inputs={"question": "is fleet cover appropriate?"},
        context_events=[EventRef(event_id=str(uuid.uuid4()), seq=1)],
        risk_schema_refs=["client.industry", "client.headcount"],
        tenant_config={"submit_to_market_threshold": 250_000},
        budget=InvocationBudget(
            max_input_tokens=4000, max_output_tokens=1000, max_wall_ms=15_000
        ),
        caller=Actor(kind="human", id="u1", name="Broker"),
        ts=datetime.now(timezone.utc),
    )
    kwargs.update(overrides)
    return ContextEnvelope(**kwargs)


# ---------- 1. ContextEnvelope round-trip ------------------------------------


def test_context_envelope_round_trips_through_json_schema():
    env = _base_envelope()
    dumped = env.model_dump(mode="json")
    text = json.dumps(dumped)
    parsed = json.loads(text)
    revived = ContextEnvelope.model_validate(parsed)
    assert revived == env


def test_context_envelope_missing_required_field_raises():
    with pytest.raises(ValidationError):
        ContextEnvelope(
            envelope_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            class_key="structure_risk",
            intent="x",
            budget=InvocationBudget(
                max_input_tokens=1, max_output_tokens=1, max_wall_ms=1
            ),
            # caller missing
            ts=datetime.now(timezone.utc),
        )


def test_context_envelope_rejects_unknown_class():
    with pytest.raises(ValidationError):
        _base_envelope(class_key="not-a-real-class")


# ---------- 2. OutputContract & pair validation ------------------------------


def _output_for(env: ContextEnvelope, **overrides) -> OutputContract:
    kwargs = dict(
        envelope_id=env.envelope_id,
        class_key=env.class_key,
        decision="approve",
        payload={"result": "ok"},
        citations=[Citation(kind="event", ref=env.context_events[0].event_id)],
        notes="test",
        tokens_used=TokenCount(input=100, output=200),
        wall_ms=42,
        provider_meta={"mocked": True},
    )
    kwargs.update(overrides)
    return OutputContract(**kwargs)


def test_output_contract_round_trips():
    env = _base_envelope()
    out = _output_for(env)
    dumped = out.model_dump(mode="json")
    revived = OutputContract.model_validate(dumped)
    assert revived == out


def test_output_contract_mismatched_envelope_id_fails_pair_validation():
    env = _base_envelope()
    other_env = _base_envelope()  # different envelope_id
    out = _output_for(other_env)  # its envelope_id != env.envelope_id
    with pytest.raises(ValueError, match="envelope_id"):
        validate_output_matches_envelope(env, out)


def test_output_contract_class_mismatch_is_not_pair_error():
    """class_key drift is a confidence signal, not a validation error."""
    env = _base_envelope()
    out = _output_for(env, class_key="claim_admin")  # different class
    # Should not raise — pair validation only checks envelope_id.
    validate_output_matches_envelope(env, out)


# ---------- 3. Approval round-trip -------------------------------------------


def test_approval_round_trips():
    a = Approval(
        envelope_id=uuid.uuid4(),
        approver=Actor(kind="human", id="op1", name="Ops Reviewer"),
        decision="edit",
        edits={"payload.result": "revised"},
        reason="minor wording change",
        ts=datetime.now(timezone.utc),
    )
    dumped = a.model_dump(mode="json")
    revived = Approval.model_validate(dumped)
    assert revived == a


def test_approval_rejects_unknown_decision():
    with pytest.raises(ValidationError):
        Approval(
            envelope_id=uuid.uuid4(),
            approver=Actor(kind="human", id="op1", name="op"),
            decision="acquiesce",   # not in the literal set
            ts=datetime.now(timezone.utc),
        )
