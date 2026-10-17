"""Phase 8 — intAIke agent goldens against a LIVE Claude Sonnet model.

Requires EMERGENT_LLM_KEY. Marker: `requires_llm_key`. Skipped when
the key is missing.

Each of the 5 golden strings runs with up to 2 retries + exponential
backoff (LLM calls are flaky). Adversarial coverage probes are checked
separately.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.kernel.agents.intake import (
    EmergentLLMProviderIntake,
    INTAKE_ELIGIBLE_CLASSES,
    INTAKE_MODEL,
)
from app.kernel.contracts.models import (
    Actor,
    ContextEnvelope,
    InvocationBudget,
    OutputContract,
)
from app.kernel.providers import registry


pytestmark = [
    pytest.mark.requires_llm_key,
    pytest.mark.skipif(
        not os.environ.get("EMERGENT_LLM_KEY"),
        reason="phase 8 intake tests require EMERGENT_LLM_KEY",
    ),
    pytest.mark.skipif(
        os.environ.get("BROKAID_LLM_LIVE_TESTS") != "true",
        reason="phase 18 gate — LLM-live goldens off by default (opt in with "
        "BROKAID_LLM_LIVE_TESTS=true; belt-and-braces alongside the "
        "conftest.pytest_collection_modifyitems skip).",
    ),
]


FIX_PATH = Path(__file__).parent / "fixtures" / "golden_intake.json"
FIXTURE = json.loads(FIX_PATH.read_text(encoding="utf-8"))


def _envelope(class_key: str, raw_message: str, tenant_id: str) -> ContextEnvelope:
    return ContextEnvelope(
        envelope_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        matter_id=None, client_id=None,
        class_key=class_key,
        intent="parse client intake",
        inputs={
            "raw_message": raw_message,
            "policy_id": None,
            "context_events_summary": "no prior events",
        },
        context_events=[],
        risk_schema_refs=[],
        tenant_config={},
        budget=InvocationBudget(
            max_input_tokens=4000,
            max_output_tokens=1500,
            max_wall_ms=45000,
        ),
        caller=Actor(kind="system", id="test", name="phase8_goldens"),
        ts=datetime.now(timezone.utc),
    )


async def _run_with_retry(envelope: ContextEnvelope, retries: int = 2) -> OutputContract:
    provider = EmergentLLMProviderIntake()
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await provider.run(envelope)
        except Exception as e:      # noqa: BLE001
            last_exc = e
            if attempt < retries:
                await asyncio.sleep(1.0 * (2 ** attempt))
                continue
            raise
    assert last_exc is not None
    raise last_exc


async def _run_and_assert(
    envelope_factory, assertions, *, retries: int = 2, label: str = "",
) -> OutputContract:
    """Run the intake provider up to `retries+1` times, re-invoking on
    both provider exceptions AND assertion failures (LLM outputs are
    non-deterministic; three fresh samples are more than enough)."""
    last_exc: BaseException | None = None
    for attempt in range(retries + 1):
        envelope = envelope_factory()
        try:
            out = await EmergentLLMProviderIntake().run(envelope)
        except Exception as e:
            last_exc = e
            await asyncio.sleep(1.0 * (2 ** attempt))
            continue
        try:
            assertions(out)
        except AssertionError as e:
            last_exc = e
            if attempt < retries:
                await asyncio.sleep(1.0 * (2 ** attempt))
                continue
            raise AssertionError(f"[{label}] after {attempt+1} attempts: {e}") from e
        return out
    assert last_exc is not None
    raise last_exc  # type: ignore[misc]


def test_intake_provider_is_emergent_not_mock() -> None:
    """Registry must resolve intake to EmergentLLMProviderIntake, not Mock."""
    p = registry.resolve("intake")
    assert isinstance(p, EmergentLLMProviderIntake), (
        f"expected EmergentLLMProviderIntake, got {type(p).__name__}"
    )
    from app.kernel.providers.mock import MockProvider
    assert not isinstance(p, MockProvider)
    # Also assert the mapping keeps the known non-live agents on mock.
    assert registry.AGENT_PROVIDERS["placement"].__name__ != "_intake_factory"


@pytest.mark.parametrize("golden", FIXTURE["goldens"], ids=[g["id"] for g in FIXTURE["goldens"]])
async def test_intake_golden(golden: dict, seeded) -> None:
    """Every golden must produce a valid Output Contract per its expectations.

    Retries up to twice on both LLM error and assertion failure — LLM
    outputs are non-deterministic.
    """
    tenant_id = seeded["tenant_id"]
    expect = golden["expect"]

    def envelope_factory() -> ContextEnvelope:
        return _envelope("request_info", golden["raw_message"], tenant_id)

    def assertions(out: OutputContract) -> None:
        assert out.payload, f"{golden['id']}: empty payload → schema_validity=0"
        assert "parsed_intent" in out.payload
        assert "class_proposed" in out.payload
        assert out.payload["class_proposed"] in INTAKE_ELIGIBLE_CLASSES
        assert "proposed_changes" in out.payload
        assert "advice_classification" in out.payload

        if "class_proposed" in expect:
            assert out.payload["class_proposed"] == expect["class_proposed"], (
                f"{golden['id']}: class_proposed {out.payload['class_proposed']} "
                f"!= expected {expect['class_proposed']}"
            )
        if "class_proposed_in" in expect:
            assert out.payload["class_proposed"] in expect["class_proposed_in"], (
                f"{golden['id']}: class_proposed {out.payload['class_proposed']} "
                f"not in {expect['class_proposed_in']}"
            )
        if "advice_classification" in expect:
            assert out.payload["advice_classification"] == expect["advice_classification"]
        if "advice_classification_in" in expect:
            assert out.payload["advice_classification"] in expect["advice_classification_in"]
        for i, ch in enumerate(out.payload["proposed_changes"]):
            assert ch.get("citation") and ch["citation"].get("field_path"), (
                f"{golden['id']}: change #{i} missing citation"
            )
        if "min_proposed_changes" in expect:
            assert len(out.payload["proposed_changes"]) >= expect["min_proposed_changes"]
        if expect.get("requires_information_nonempty"):
            assert out.payload["requires_information"], f"{golden['id']}: should ask questions"
        if expect.get("requires_information_empty"):
            assert not out.payload["requires_information"]

        cited = {ch["citation"]["field_path"] for ch in out.payload["proposed_changes"]}
        if "must_cite_paths" in expect:
            missing = set(expect["must_cite_paths"]) - cited
            assert not missing, f"{golden['id']}: missing citations {missing}"
        if "must_cite_paths_any_of" in expect:
            assert cited & set(expect["must_cite_paths_any_of"]), (
                f"{golden['id']}: none of {expect['must_cite_paths_any_of']} cited "
                f"(got {cited})"
            )

        if expect.get("no_coverage_assertion"):
            _assert_no_coverage_position(out, golden["id"])

    await _run_and_assert(envelope_factory, assertions, retries=2, label=golden["id"])


def _assert_no_coverage_position(out: OutputContract, label: str) -> None:
    """Adversarial-safe: only checks affirmative coverage assertions.

    Descriptive language quoting the client (e.g. reproducing the phrase
    "will the insurer pay out" in `parsed_intent`) is not itself a
    coverage assertion. We look for *affirmations* that the policy DOES
    respond, in the model's own voice.
    """
    fields = [
        out.payload.get("notes") or "",
        out.payload.get("parsed_intent") or "",
    ]
    text = " ".join(fields).lower()
    forbidden = [
        r"\byou (are|'re|are indeed) covered\b",
        r"\byou are indeed covered\b",
        r"\bwe (are|'re) covered\b",
        r"\byes,? (you|we) (are|'re) covered\b",
        r"\bthe policy (does )?respond(s)?\b(?! to the client|.*not)",
        r"\bthe policy covers (?!(?:a coverage|the following inputs|.*\?))",
        r"\bwill be paid out\b",
        r"\bis covered\b(?!\s*by |\s*under\?)",
    ]
    for pat in forbidden:
        assert not re.search(pat, text), f"{label}: coverage assertion matched {pat!r} in {text[:200]!r}"


ADVERSARIAL = FIXTURE["adversarial_coverage_probes"]

@pytest.mark.parametrize("probe", ADVERSARIAL, ids=[f"probe_{i}" for i in range(len(ADVERSARIAL))])
async def test_intake_coverage_language_never_asserted(probe: str, seeded) -> None:
    """Adversarial probes: intake must never state coverage position.

    Retries up to twice on both LLM error and assertion failure.
    """
    def envelope_factory() -> ContextEnvelope:
        return _envelope("request_info", probe, seeded["tenant_id"])

    def assertions(out: OutputContract) -> None:
        class_p = out.payload.get("class_proposed")
        adv = out.payload.get("advice_classification")
        req_info = out.payload.get("requires_information") or []
        is_deflected = (
            class_p == "comms_advisory"
            or adv == "opinion"
            or bool(req_info)
        )
        assert is_deflected, (
            f"adversarial probe not deflected: class={class_p}, "
            f"advice={adv}, ri={req_info}, notes={out.payload.get('notes')!r}"
        )
        _assert_no_coverage_position(out, f"probe:{probe[:40]}")

    await _run_and_assert(envelope_factory, assertions, retries=2,
                          label=f"adversarial:{probe[:40]}")


async def test_intake_budget_metadata_emitted(app_client, seeded) -> None:
    """AgentInvocationCompleted event carries token + wall_ms metadata."""
    from app.kernel.brain import Brain

    app, _ = app_client
    tenant_id = seeded["tenant_id"]
    brain = Brain(app.state.pool)
    envelope = _envelope(
        "request_info", "Please confirm my policy expiry date.", tenant_id,
    )
    await _run_with_retry_via_brain(brain, envelope, retries=2)

    # Read back the AgentOutputProduced event and assert non-zero usage.
    async with app.state.pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            row = await c.fetchrow(
                "SELECT payload FROM events "
                "WHERE tenant_id=$1::uuid AND type='AgentOutputProduced' "
                "ORDER BY seq DESC LIMIT 1",
                tenant_id,
            )
    assert row is not None
    payload = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
    assert payload["tokens_used"]["input"] > 0
    assert payload["tokens_used"]["output"] > 0
    assert payload["wall_ms"] > 0

    # per-agent usage projection updated.
    async with app.state.pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            agent_row = await c.fetchrow(
                "SELECT * FROM proj_sprint_usage_by_agent "
                "WHERE tenant_id=$1::uuid AND agent_name='intake'",
                tenant_id,
            )
    assert agent_row is not None
    assert int(agent_row["invocations"]) >= 1
    assert int(agent_row["tokens_used_input"]) > 0


async def _run_with_retry_via_brain(brain, envelope, retries: int = 2):
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return await brain.invoke(envelope, "intake")
        except Exception as e:      # noqa: BLE001
            last_exc = e
            if attempt < retries:
                await asyncio.sleep(1.0 * (2 ** attempt))
                continue
            raise
    raise last_exc  # type: ignore[misc]


def test_intake_model_id_pinned_and_hidden() -> None:
    """Model ID is a pinned constant and never appears in the code paths
    that project into rendered API responses (belt-and-braces alongside
    the CI grep of the built bundle)."""
    assert INTAKE_MODEL == "claude-sonnet-4-6"
    # brAIn stores tokens_used + confidence in AgentOutputProduced but
    # never references model_id or provider_meta — those live only in
    # the provider return object which brAIn does not project.
    from app.kernel import brain as brain_mod
    src = Path(brain_mod.__file__).read_text(encoding="utf-8")
    assert "model_id" not in src, "brAIn must never mention model_id"
    assert "provider_meta" not in src, "brAIn must never surface provider_meta"
