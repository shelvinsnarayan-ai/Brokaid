"""Unified client-facing message release pipeline (dispatch-aware, R5b-2a).

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

R5b-2a: converted `pool` → `store` and dispatched all 4 trail appends
via `trail_dispatch.append_via_dispatch`. Under Mongo the caller
enters `use_tenant(tid)` around the emit. Queue projection stays a
best-effort side-effect (see original phase-10 comment).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.tenant_safe import TenantSafeDatabase
from app.kernel.agents.compliance import EmergentLLMProviderCompliance
from app.kernel.gate.coverage_guard import check as coverage_check
from app.kernel.guard_rewrites import (
    cache_llm_result as _cache_llm_result,
    first_block_kind as _first_block_kind,
    prescan_lookup as _prescan_lookup,
)
from app.kernel.queue import enqueue as queue_enqueue
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail import Event
from app.kernel.trail_dispatch import append_via_dispatch


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
    event: Event | dict[str, Any] | None = None
    classifier_verdict: dict[str, Any] | None = None
    spans: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None
    recommended_rewrite: str | None = None


_SEV_RANK = {"info": 0, "warn": 1, "block": 2}


def _is_mongo(store) -> bool:
    return isinstance(store, (AsyncIOMotorDatabase, TenantSafeDatabase))


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


async def _emit(store, tenant_id: str, **kw):
    """Return a namespace-wrapped dispatch result carrying all the
    kwargs so `.event_id` / `.type` / `.payload` / `.actor` /
    `.mocked` / `.idempotency_key` all work for pre-rewire callers
    that access these as attributes (backward-compat)."""
    from types import SimpleNamespace
    if _is_mongo(store):
        async with use_tenant(tenant_id):
            d = await append_via_dispatch(store, tenant_id, **kw)
    else:
        d = await append_via_dispatch(store, tenant_id, **kw)
    return SimpleNamespace(
        event_id=d.get("event_id"),
        seq=d.get("seq"),
        event_hash=d.get("event_hash"),
        type=kw.get("type"),
        actor=kw.get("actor"),
        payload=kw.get("payload"),
        mocked=kw.get("mocked", False),
        idempotency_key=kw.get("idempotency_key"),
        tenant_id=tenant_id,
    )


async def try_release(
    *,
    store=None,
    pool: object | None = None,  # backward-compat alias (R5b-2a).
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

    R5b-2a: `store` is either an pool or Motor DB / TenantSafe.
    `pool=` is accepted as a backward-compat alias for pre-rewire
    callers.
    """
    if store is None:
        store = pool
    if store is None:
        raise TypeError("try_release requires store= (or pool= legacy)")
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

    # 2. Deterministic guard-rewrite cache — bypasses the LLM when we
    #    can recognise the block reason from a canned pre-scan phrase.
    prescan = _prescan_lookup(
        draft=draft,
        jurisdiction=context.get("jurisdiction_code"),
        product_class=context.get("product_class"),
    )
    if prescan is not None:
        synth_verdict = {
            "advice_classification": "opinion",
            "classification_rationale": "deterministic pre-scan cache hit",
            "risks_flagged": prescan["risks_flagged"],
            "recommended_rewrite": prescan["recommended_rewrite"],
            "release_verdict": "rewrite",
            "_tokens_used": {"input": 0, "output": 0},
            "_wall_ms": 0,
            "_source": prescan["source"],
            "_cache_key": prescan["cache_key"],
        }
        idem_seed_c = envelope_id or str(uuid.uuid4())
        common_payload_c: dict[str, Any] = {
            "draft": draft,
            "author_agent": author_agent,
            "classification": "opinion",
            "classifier_rationale": "deterministic pre-scan cache hit",
            "risks_flagged": prescan["risks_flagged"],
            "mocked_channels_used": list(mocked_channels_used or []),
            "classifier_verdict": synth_verdict,
            "matter_id": matter_id,
            "client_id": client_id,
            "envelope_id": envelope_id,
            "guard_rewrite_source": prescan["source"],
            "block_reason_code": prescan["cache_key"]["block_reason_code"],
        }
        ev_c = await _emit(
            store, tenant_id, type="ClientMessageRewriteRequired",
            actor={"kind": "system", "id": "release", "name": "release"},
            payload={
                **common_payload_c,
                "recommended_rewrite": prescan["recommended_rewrite"],
            },
            idempotency_key=f"clientmsg:rewrite:{idem_seed_c}",
        )
        return ReleaseResult(
            released=False,
            verdict="rewrite",
            event=ev_c,
            classifier_verdict=synth_verdict,
            recommended_rewrite=prescan["recommended_rewrite"],
        )

    # 3. LLM classifier.
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
        ev = await _emit(
            store, tenant_id, type="ClientMessageReleased",
            actor=actor,
            payload={
                **common_payload,
                "message": draft,
            },
            idempotency_key=f"clientmsg:release:{idem_seed}",
        )
        return ReleaseResult(
            released=True, verdict="release",
            event=ev, classifier_verdict=verdict,
        )

    if v_kind == "rewrite":
        _cache_llm_result(
            block_reason_code=_first_block_kind(risks),
            jurisdiction=context.get("jurisdiction_code"),
            product_class=context.get("product_class"),
            rewrite=verdict.get("recommended_rewrite"),
        )
        ev = await _emit(
            store, tenant_id, type="ClientMessageRewriteRequired",
            actor=actor,
            payload={
                **common_payload,
                "recommended_rewrite": verdict.get("recommended_rewrite"),
                "guard_rewrite_source": "llm",
                "block_reason_code": _first_block_kind(risks),
            },
            idempotency_key=f"clientmsg:rewrite:{idem_seed}",
        )
        return ReleaseResult(
            released=False, verdict="rewrite",
            event=ev, classifier_verdict=verdict,
            recommended_rewrite=verdict.get("recommended_rewrite"),
        )

    # hitl (default / recommendation / classifier-unavailable)
    ev = await _emit(
        store, tenant_id, type="ClientMessageHITL",
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
    # Queue enqueue — class_key='comms_advisory'. Only under PG for now
    # (queue.enqueue is not yet dispatch-aware; R5c will address).
    queue_env_id = envelope_id or ev.event_id
    if not _is_mongo(store):
        try:
            await queue_enqueue(
                store,
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
            pass
    return ReleaseResult(
        released=False, verdict="hitl",
        event=ev, classifier_verdict=verdict,
    )
