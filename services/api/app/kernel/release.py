"""Unified client-facing message release pipeline.

Two-layer:
  1. Deterministic coverage-language guard (from phase 2). Cheap,
     always-on. If it blocks, the LLM classifier is NEVER called.
  2. complAInce LLM classifier (phase 10). Tags advice as
     `factual | opinion | recommendation` and flags risks.

Public entry: `try_release(...)`. Every code path that would emit an
event whose type is in `CLIENT_RELEASE_EVENT_TYPES` MUST go through
this module. The `test_release_pipeline_audit` test enforces that
invariant with a codebase-wide grep.

The trAIl invariant (see `app.kernel.trail.append`) rejects any
release-event write whose payload is missing `classifier_verdict`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal

import asyncpg

from app.kernel.agents.compliance import EmergentLLMProviderCompliance
from app.kernel.gate.coverage_guard import check as coverage_check
from app.kernel.queue import enqueue as queue_enqueue
from app.kernel.trail import Event, append


CLIENT_RELEASE_EVENT_TYPES: frozenset[str] = frozenset({
    "ClientMessageReleased",
    "ClientMessageRewriteRequired",
    "ClientMessageHITL",
})


ClassifierFn = Callable[..., Awaitable[dict[str, Any]]]


ReleaseVerdict = Literal[
    "release", "rewrite", "hitl", "coverage_guard_block"
]


@dataclass
class ReleaseResult:
    """The outcome of one `try_release()` call."""
    released: bool
    verdict: ReleaseVerdict
    event: Event | None = None
    classifier_verdict: dict[str, Any] | None = None
    spans: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None
    recommended_rewrite: str | None = None


_SEV_RANK = {"info": 0, "warn": 1, "block": 2}


def _max_severity(risks: list[dict[str, Any]]) -> str:
    if not risks:
        return "info"
    return max(
        (r.get("severity", "info") for r in risks),
        key=lambda s: _SEV_RANK.get(s, 0),
    )


async def _default_classifier(
    *, draft: str, context: dict[str, Any], author_agent: str,
) -> dict[str, Any]:
    return await EmergentLLMProviderCompliance().classify(
        draft=draft, context=context, author_agent=author_agent,
    )


async def try_release(
    *,
    pool: asyncpg.Pool,
    tenant_id: str,
    draft: str,
    context: dict[str, Any],
    author_agent: str,
    matter_id: str | None = None,
    envelope_id: str | None = None,
    client_id: str | None = None,
    mocked_channels_used: list[str] | None = None,
    classifier: ClassifierFn | None = None,
) -> ReleaseResult:
    """Run one client-facing message through the two-layer release check.

    Sequence (in exact order):
      a. Deterministic coverage-language guard. If it blocks, return
         `{released: false, verdict: 'coverage_guard_block', spans}`
         WITHOUT invoking the classifier.
      b. LLM classifier. Runs only if (a) passed.
      c. Combine:
         * `release` + max severity ≤ warn → emit
           `ClientMessageReleased` and return `released=True`.
         * `rewrite` → emit `ClientMessageRewriteRequired` and return
           `released=False` with `recommended_rewrite`.
         * `hitl` → emit `ClientMessageHITL` and enqueue on the
           operator queue with `class_key='comms_advisory'`.
    """
    mocked_channels_used = list(mocked_channels_used or [])
    classifier_fn = classifier or _default_classifier

    # 1. Deterministic guard first — cheap, always-on.
    guard = coverage_check(draft)
    if not guard["passed"]:
        return ReleaseResult(
            released=False,
            verdict="coverage_guard_block",
            spans=list(guard["blocked_spans"]),
            reason=guard["reason"],
        )

    # 2. LLM classifier.
    verdict = await classifier_fn(
        draft=draft, context=context, author_agent=author_agent,
    )
    v_kind = verdict.get("release_verdict", "hitl")
    risks = verdict.get("risks_flagged") or []
    max_sev = _max_severity(risks)

    # Belt-and-braces: if any block risk snuck through, force off release.
    if v_kind == "release" and max_sev == "block":
        v_kind = "rewrite"
        verdict["release_verdict"] = "rewrite"

    now = datetime.now(timezone.utc)
    idem_seed = envelope_id or str(uuid.uuid4())
    common_payload: dict[str, Any] = {
        "draft": draft,
        "author_agent": author_agent,
        "classification": verdict.get("advice_classification"),
        "classifier_rationale": verdict.get("classification_rationale"),
        "risks_flagged": risks,
        "mocked_channels_used": mocked_channels_used,
        "classifier_verdict": verdict,
        "matter_id": matter_id,
        "client_id": client_id,
        "envelope_id": envelope_id,
    }

    actor = {"kind": "system", "id": "release", "name": "release"}

    if v_kind == "release":
        ev = await append(
            pool, tenant_id, "ClientMessageReleased",
            actor=actor,
            payload={
                **common_payload,
                "message": draft,
            },
            idempotency_key=f"clientmsg:release:{idem_seed}",
        )
        return ReleaseResult(
            released=True,
            verdict="release",
            event=ev,
            classifier_verdict=verdict,
        )

    if v_kind == "rewrite":
        ev = await append(
            pool, tenant_id, "ClientMessageRewriteRequired",
            actor=actor,
            payload={
                **common_payload,
                "recommended_rewrite": verdict.get("recommended_rewrite"),
            },
            idempotency_key=f"clientmsg:rewrite:{idem_seed}",
        )
        return ReleaseResult(
            released=False,
            verdict="rewrite",
            event=ev,
            classifier_verdict=verdict,
            recommended_rewrite=verdict.get("recommended_rewrite"),
        )

    # hitl (default / recommendation / classifier-unavailable)
    ev = await append(
        pool, tenant_id, "ClientMessageHITL",
        actor=actor,
        payload={
            **common_payload,
            "hitl_reason": (
                "recommendation_forces_soa"
                if verdict.get("advice_classification") == "recommendation"
                else "classifier_hitl"
            ),
        },
        idempotency_key=f"clientmsg:hitl:{idem_seed}",
    )
    # Queue enqueue — class_key='comms_advisory'. Envelope for the queue
    # card is the release event id itself when the caller has no
    # upstream envelope_id (comms_service drafts).
    queue_env_id = envelope_id or ev.event_id
    try:
        await queue_enqueue(
            pool,
            tenant_id=tenant_id,
            envelope_id=queue_env_id,
            class_key="comms_advisory",
            proposal_ref=ev.event_id,
            matter_id=matter_id,
            client_id=client_id,
            gate_reason="compliance_hitl",
            confidence=None,
            routed_to_role="compliance",
            hitl_since=now,
        )
    except Exception:
        # Non-fatal: the ledger event is authoritative. Queue projection
        # is best-effort here.
        pass
    return ReleaseResult(
        released=False,
        verdict="hitl",
        event=ev,
        classifier_verdict=verdict,
    )
