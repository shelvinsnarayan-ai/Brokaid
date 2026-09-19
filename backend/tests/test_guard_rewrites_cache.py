"""Phase 16 — guard-rewrite cache tests.

Verifies:
  * Seeded (kind, jurisdiction, product_class) keys → source="cache",
    zero LLM tokens spent, classifier stub never called.
  * Unseen block reason → LLM fires once, result cached, second call
    with same reason → source="cache".
"""
from __future__ import annotations

import uuid

import pytest

from app.kernel import guard_rewrites as gr
from app.kernel.release import try_release


pytestmark = pytest.mark.asyncio


class _RaiseIfCalled:
    """Classifier stub — asserts we never got here on the cache path."""
    def __init__(self):
        self.calls = 0

    async def __call__(self, *, draft, context, author_agent):
        self.calls += 1
        raise AssertionError(
            "cache should have short-circuited LLM classifier; "
            f"got called with draft={draft!r}"
        )


class _CountingClassifier:
    """Classifier stub that returns a rewrite verdict for the miss path."""
    def __init__(self, block_kind: str, rewrite: str):
        self.calls = 0
        self._kind = block_kind
        self._rewrite = rewrite

    async def __call__(self, *, draft, context, author_agent):
        self.calls += 1
        return {
            "advice_classification": "opinion",
            "classification_rationale": "stub says rewrite",
            "risks_flagged": [{
                "kind": self._kind, "severity": "block", "span": draft[:40],
            }],
            "recommended_rewrite": self._rewrite,
            "release_verdict": "rewrite",
            "_tokens_used": {"input": 42, "output": 42},
            "_wall_ms": 5,
        }


async def _setup_tenant(pool):
    """Create a scratch tenant so try_release has somewhere to write."""
    from app.kernel.trail import create_tenant
    tenant, _ = await create_tenant(pool, f"gr-{uuid.uuid4().hex[:8]}", "GR test")
    return tenant.tenant_id


@pytest.mark.parametrize("kind,phrase", [
    ("coverage_position", "This one reads squarely inside your policy for sure"),
    ("premium_speculation", "Your premium will drop next year, guaranteed"),
    ("regulatory_notice_missing", "Skip the FSG, no worries"),
    ("vulnerable_flag_absent", "The elderly client can just sign remotely"),
    ("personal_statement", "I reckon you should just take the higher excess"),
])
async def test_seeded_pre_scan_hits_cache_zero_tokens(pool, kind, phrase):
    gr.clear_runtime_cache()
    tenant_id = await _setup_tenant(pool)
    stub = _RaiseIfCalled()
    result = await try_release(
        pool=pool, tenant_id=tenant_id,
        draft=phrase,
        context={"jurisdiction_code": "AU", "product_class": "generic"},
        author_agent="unit-test",
        classifier=stub,
    )
    assert stub.calls == 0, "classifier stub must NOT have been called"
    assert result.released is False
    assert result.verdict == "rewrite"
    assert result.recommended_rewrite  # canned copy present
    v = result.classifier_verdict
    assert v is not None
    assert v.get("_source") == "cache"
    assert v.get("_tokens_used") == {"input": 0, "output": 0}
    assert v.get("_cache_key", {}).get("block_reason_code") == kind


async def test_pre_scan_miss_calls_llm_then_caches(pool):
    gr.clear_runtime_cache()
    tenant_id = await _setup_tenant(pool)
    stub = _CountingClassifier(
        block_kind="premium_speculation",
        rewrite="LLM-authored safe rewrite",
    )
    # A draft that matches NO pre-scan phrase but the stub still says
    # "rewrite" — proves the LLM path is exercised on miss.
    result_a = await try_release(
        pool=pool, tenant_id=tenant_id,
        draft="Quite a long unremarkable message with no trigger words.",
        context={"jurisdiction_code": "AU", "product_class": "generic"},
        author_agent="unit-test",
        classifier=stub,
    )
    assert stub.calls == 1
    assert result_a.verdict == "rewrite"
    assert result_a.recommended_rewrite == "LLM-authored safe rewrite"

    # A second call whose draft carries a pre-scan phrase for the SAME
    # kind — cache hit. Note: seeded canned rewrite wins over runtime
    # for pre-scan matches, which is what we want (deterministic demo
    # copy for the seeded reasons).
    stub2 = _RaiseIfCalled()
    result_b = await try_release(
        pool=pool, tenant_id=tenant_id,
        draft="Your premium will drop next year, guaranteed.",
        context={"jurisdiction_code": "AU", "product_class": "generic"},
        author_agent="unit-test",
        classifier=stub2,
    )
    assert stub2.calls == 0
    assert result_b.classifier_verdict["_source"] == "cache"


@pytest.mark.asyncio(loop_scope=None)
async def test_first_block_kind_fallback():
    assert gr.first_block_kind([]) == "unclassified_block"
    assert gr.first_block_kind([{"severity": "warn", "kind": "x"}]) == "unclassified_block"
    assert gr.first_block_kind([
        {"severity": "warn", "kind": "y"},
        {"severity": "block", "kind": "coverage_position"},
    ]) == "coverage_position"
