"""Phase 10 — try_release() pipeline tests.

Covers:

  * Deterministic guard runs FIRST; classifier NEVER called if the
    guard blocks (patch the classifier to raise and confirm).
  * Every ClientMessage* event carries `classifier_verdict` (writes
    without it raise `ClassifierVerdictMissing`).
  * `recommendation` classification forces HITL, and the operator
    queue receives a `class_key='comms_advisory'` card.
  * Codebase audit: every direct `append(...)` call whose event type
    is in `CLIENT_RELEASE_EVENT_TYPES` lives inside
    `app/kernel/release.py`. Any other emitter is a bug.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.kernel.release import (
    CLIENT_RELEASE_EVENT_TYPES,
    ReleaseResult,
    try_release,
)
from app.kernel.trail import ClassifierVerdictMissing, append


REPO_ROOT = Path(__file__).resolve().parents[1]        # services/api


# ---- Helpers --------------------------------------------------------------


async def _make_stub_classifier(verdict: dict[str, Any]):
    async def _stub(*, draft, context, author_agent):
        return dict(verdict)
    return _stub


async def _raising_classifier(*, draft, context, author_agent):  # noqa: ARG001
    raise RuntimeError("classifier should not have been called")


# ---- 1. Deterministic-first invariant -------------------------------------


@pytest.mark.asyncio
async def test_release_pipeline_deterministic_first_then_llm(app_client, seeded) -> None:
    """Red-team string → guard blocks; classifier is NEVER invoked."""
    app, _ = app_client
    tenant_id = seeded["tenant_id"]
    result = await try_release(
        pool=app.state.pool,
        tenant_id=tenant_id,
        draft="You are covered under this policy.",
        context={"matter_kind": "claim"},
        author_agent="liaison",
        classifier=_raising_classifier,
    )
    assert result.released is False
    assert result.verdict == "coverage_guard_block"
    assert result.event is None
    assert result.spans, "expected at least one blocked span"
    assert result.classifier_verdict is None


# ---- 2. Classifier release path -------------------------------------------


@pytest.mark.asyncio
async def test_release_pipeline_release_path(app_client, seeded) -> None:
    """Guard passes + classifier release → ClientMessageReleased on ledger."""
    app, _ = app_client
    stub = await _make_stub_classifier({
        "advice_classification": "factual",
        "classification_rationale": "Neutral factual update.",
        "risks_flagged": [],
        "recommended_rewrite": None,
        "release_verdict": "release",
        "_tokens_used": {"input": 100, "output": 40},
        "_wall_ms": 250,
    })
    result = await try_release(
        pool=app.state.pool,
        tenant_id=seeded["tenant_id"],
        draft="Your broker will call you tomorrow to walk you through the quote.",
        context={"matter_kind": "general"},
        author_agent="liaison",
        classifier=stub,
    )
    assert result.released is True
    assert result.verdict == "release"
    assert result.event is not None
    assert result.event.type == "ClientMessageReleased"
    assert "classifier_verdict" in result.event.payload


# ---- 3. Rewrite path ------------------------------------------------------


@pytest.mark.asyncio
async def test_release_pipeline_rewrite_path(app_client, seeded) -> None:
    """Classifier rewrite → ClientMessageRewriteRequired with rewrite."""
    app, _ = app_client
    stub = await _make_stub_classifier({
        "advice_classification": "opinion",
        "classification_rationale": "Personal opinion smuggled in.",
        "risks_flagged": [{
            "kind": "personal_statement",
            "span": "in my view",
            "severity": "block",
        }],
        "recommended_rewrite": "The insurer will review your file. We'll write back once they respond.",
        "release_verdict": "rewrite",
        "_tokens_used": {"input": 120, "output": 60},
        "_wall_ms": 300,
    })
    result = await try_release(
        pool=app.state.pool,
        tenant_id=seeded["tenant_id"],
        draft="In my view the insurer should side with you here.",
        context={"matter_kind": "claim"},
        author_agent="liaison",
        classifier=stub,
    )
    assert result.released is False
    assert result.verdict == "rewrite"
    assert result.event.type == "ClientMessageRewriteRequired"
    assert result.recommended_rewrite and "insurer will review" in result.recommended_rewrite


# ---- 4. Recommendation forces HITL + queue card enqueued ------------------


@pytest.mark.asyncio
async def test_recommendation_forces_hitl(app_client, seeded) -> None:
    """Classification=recommendation → verdict=hitl → queue card enqueued."""
    app, _ = app_client
    tenant_id = seeded["tenant_id"]
    stub = await _make_stub_classifier({
        "advice_classification": "recommendation",
        "classification_rationale": "Recommends a specific broker action.",
        "risks_flagged": [],
        "recommended_rewrite": None,
        "release_verdict": "hitl",
        "_tokens_used": {"input": 100, "output": 40},
        "_wall_ms": 250,
    })
    result = await try_release(
        pool=app.state.pool,
        tenant_id=tenant_id,
        draft="We recommend increasing the sum insured to $2M before your next contract.",
        context={"matter_kind": "general"},
        author_agent="liaison",
        classifier=stub,
    )
    assert result.released is False
    assert result.verdict == "hitl"
    assert result.event.type == "ClientMessageHITL"

    # The operator queue must have a comms_advisory card keyed on the
    # envelope for this release.
    async with app.state.pool.acquire() as c:
        async with c.transaction():
            await c.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            row = await c.fetchrow(
                "SELECT class_key FROM proj_queue "
                "WHERE tenant_id=$1::uuid AND proposal_ref=$2::uuid",
                tenant_id, result.event.event_id,
            )
    assert row is not None, "expected a queue card for the HITL release"
    assert row["class_key"] == "comms_advisory"


# ---- 5. Ledger invariant — writes without classifier_verdict raise --------


@pytest.mark.asyncio
async def test_every_client_release_event_carries_classifier_verdict(
    app_client, seeded,
) -> None:
    """Direct append(ClientMessageReleased, ...) without classifier_verdict raises."""
    app, _ = app_client
    tenant_id = seeded["tenant_id"]
    with pytest.raises(ClassifierVerdictMissing):
        await append(
            app.state.pool, tenant_id, "ClientMessageReleased",
            actor={"kind": "system", "id": "test", "name": "test"},
            payload={"message": "hi"},                 # verdict missing!
            idempotency_key="test:no-verdict:1",
        )
    for release_type in CLIENT_RELEASE_EVENT_TYPES:
        with pytest.raises(ClassifierVerdictMissing):
            await append(
                app.state.pool, tenant_id, release_type,
                actor={"kind": "system", "id": "test", "name": "test"},
                payload={},
                idempotency_key=f"test:no-verdict:{release_type}",
            )


# ---- 6. Codebase audit — every ClientMessage* emitter uses try_release() --


_APPEND_CALL_RE = re.compile(
    r"""append\s*\(          # append( ...
        [^,]*,\s*            # pool,
        [^,]*,\s*            # tenant_id,
        (?:"([A-Za-z_]+)"|'([A-Za-z_]+)')   # type as bare string literal
    """,
    re.VERBOSE,
)


def _iter_py_files() -> list[Path]:
    app_root = REPO_ROOT / "app"
    files: list[Path] = []
    for p in app_root.rglob("*.py"):
        # Tests live outside app/, exclude legacy paths.
        if "__pycache__" in p.parts:
            continue
        files.append(p)
    return files


def test_release_pipeline_audit() -> None:
    """Every append() call with a ClientMessage* type MUST be inside release.py."""
    release_module = (
        REPO_ROOT / "app" / "kernel" / "release.py"
    ).resolve()
    offenders: list[str] = []
    for path in _iter_py_files():
        text = path.read_text(encoding="utf-8")
        for m in _APPEND_CALL_RE.finditer(text):
            event_type = m.group(1) or m.group(2)
            if event_type in CLIENT_RELEASE_EVENT_TYPES:
                if path.resolve() != release_module:
                    line = text[: m.start()].count("\n") + 1
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{line} → {event_type}")
    assert not offenders, (
        "ClientMessage* emitters found outside app/kernel/release.py:\n  "
        + "\n  ".join(offenders)
    )


def test_release_pipeline_audit_grep_backstop() -> None:
    """Ripgrep/grep backstop — no ClientMessage* string in any code file
    outside release.py, except tests and the audit test itself."""
    forbidden = "|".join(sorted(CLIENT_RELEASE_EVENT_TYPES))
    proc = subprocess.run(
        [
            "grep", "-r", "-l", "-E",
            "--include=*.py",
            "--exclude-dir=__pycache__",
            forbidden,
            "app",
        ],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    hits = [
        h for h in proc.stdout.strip().splitlines()
        if h and h != "app/kernel/release.py" and not h.endswith("trail.py")
    ]
    assert not hits, (
        f"ClientMessage* string appears outside release.py + trail.py: {hits}"
    )
