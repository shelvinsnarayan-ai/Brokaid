"""Phase 16 — deterministic guard-rewrite cache.

Purpose
-------
Skip the complAInce LLM classifier when we can already recognise the
block reason deterministically and return a canned safe rewrite. This
serves two goals:

1. Zero-token cost on the demo-critical block paths (the classifier is
   the most expensive call in the release pipeline).
2. Reproducible rewrite text at SIRC — no run-to-run drift on the
   demo scenes.

Block reason code derivation
----------------------------
Per decision from orchestrator (phase 16 lock):

    block_reason_code := first_block_severity_risk.kind
    fallback          := "unclassified_block"

Cache key = (first_block_risk_kind, jurisdiction_code, product_class)

Because the classifier is the source of truth for `risks_flagged`, we
cannot see the reason code before running it in the general case. To
still deliver a "cached-first, LLM only on cache miss" experience we
run a small deterministic pre-scanner over the draft that maps
tell-tale phrases onto the same `kind` enum the classifier uses. On a
pre-scan match the LLM is skipped and a canned rewrite is returned
with `source: "cache"`. On a miss the LLM runs; its output is stored
in a runtime cache for future demos of the exact same reason code.

The pre-scan phrases below are all fictional demo triggers — no real
client language is baked in.
"""
from __future__ import annotations

from typing import Any


# ---- Risk kinds (mirrors compliance.py::_RISK_KINDS) -----------------------
RISK_KINDS = (
    "coverage_position",
    "premium_speculation",
    "regulatory_notice_missing",
    "vulnerable_flag_absent",
    "personal_statement",
)
UNCLASSIFIED = "unclassified_block"


# ---- Deterministic pre-scan phrases ---------------------------------------
# Case-insensitive substring match. First hit wins. Ordering is the priority
# order — put the highest-severity kinds first.
_KIND_PHRASES: dict[str, tuple[str, ...]] = {
    "coverage_position": (
        # Phrases that hint at coverage position without literally tripping
        # the deterministic coverage_guard's rule set (see
        # `app.kernel.gate.coverage_guard`). Coverage_guard is the first
        # layer; if it already catches the sentence, our LLM-avoidance
        # cache is moot.
        "definitely in scope",
        "squarely inside your policy",
        "reads on your schedule",
        "clearly within your cover",
    ),
    "premium_speculation": (
        "premium will drop",
        "premium will fall",
        "cheaper next year",
        "save you money",
        "guarantee a lower",
    ),
    "regulatory_notice_missing": (
        "no fsg",
        "skip the fsg",
        "no pds",
        "without the pds",
    ),
    "vulnerable_flag_absent": (
        "elderly client",
        "hearing impaired",
        "cognitive impairment",
    ),
    "personal_statement": (
        "i reckon",
        "i think you should",
        "if i were you",
        "personally i would",
    ),
}


def deterministic_scan(draft: str) -> str | None:
    """Return the matching `risks_flagged.kind` if any pre-scan phrase
    is present in the draft, else None.
    """
    if not draft:
        return None
    d = draft.lower()
    for kind, phrases in _KIND_PHRASES.items():
        for p in phrases:
            if p in d:
                return kind
    return None


# ---- Seeded canned rewrites (demo scenes) ---------------------------------
# Keys are (risk_kind, jurisdiction_code, product_class). Rewrites are
# plain fictional demo copy — a phrase Shelvin can point at on the SIRC
# floor and say "and this ships offline, deterministically, no LLM".
CANNED_REWRITES: dict[tuple[str, str, str], str] = {
    ("coverage_position", "AU", "generic"):
        "I can't confirm the cover position on the spot — I'll come back "
        "once the underwriter's written response lands.",
    ("premium_speculation", "AU", "generic"):
        "Premium outcomes are the underwriter's call. Rather than speculate, "
        "I'll share the exact figure the moment we hold a quote.",
    ("regulatory_notice_missing", "AU", "generic"):
        "Our Financial Services Guide (FSG) is available on request and "
        "applies to any advice discussed here.",
    ("vulnerable_flag_absent", "AU", "generic"):
        "I've flagged this thread for compliance review so we handle your "
        "circumstances with the care they warrant.",
    ("personal_statement", "AU", "generic"):
        "Rather than share my personal view, I'll confirm the licensee's "
        "position and revert within one business day.",

    ("coverage_position", "SG", "generic"):
        "MAS requires cover position to be confirmed in writing by the "
        "underwriter — I'll revert once we have that in hand.",
    ("premium_speculation", "SG", "generic"):
        "Premium indications will only be firm after the insurer responds; "
        "I won't put a number to it ahead of that.",
    ("regulatory_notice_missing", "SG", "generic"):
        "Our MAS Financial Advisers Act disclosure is available on request "
        "and applies to any advice touched on here.",
    ("vulnerable_flag_absent", "SG", "generic"):
        "Flagged for compliance review under our vulnerable-client "
        "protocol so we handle this appropriately.",
    ("personal_statement", "SG", "generic"):
        "I'll defer to written guidance from the underwriter and compliance "
        "before sharing any position.",
}


def seeded_keys() -> list[tuple[str, str, str]]:
    return list(CANNED_REWRITES.keys())


# ---- Runtime cache: LLM outputs learned during a session ------------------
_runtime_cache: dict[tuple[str, str, str], str] = {}


def clear_runtime_cache() -> None:
    _runtime_cache.clear()


def _resolve_key(
    kind: str, jurisdiction: str | None, product_class: str | None,
) -> tuple[str, str, str]:
    return (kind, jurisdiction or "AU", product_class or "generic")


def _seeded_or_generic(key: tuple[str, str, str]) -> str | None:
    if key in CANNED_REWRITES:
        return CANNED_REWRITES[key]
    # Fall back to (kind, jurisdiction, "generic") then (kind, "AU", "generic").
    generic = (key[0], key[1], "generic")
    if generic in CANNED_REWRITES:
        return CANNED_REWRITES[generic]
    au_generic = (key[0], "AU", "generic")
    if au_generic in CANNED_REWRITES:
        return CANNED_REWRITES[au_generic]
    return None


def prescan_lookup(
    *,
    draft: str,
    jurisdiction: str | None = None,
    product_class: str | None = None,
) -> dict[str, Any] | None:
    """Deterministic pre-classifier + seeded cache lookup.

    Returns a shape ready to inline into a `ReleaseResult.classifier_verdict`
    on cache hit, or None on miss.
    """
    kind = deterministic_scan(draft)
    if kind is None:
        return None
    key = _resolve_key(kind, jurisdiction, product_class)
    canned = _runtime_cache.get(key) or _seeded_or_generic(key)
    if canned is None:
        return None
    return {
        "source": "cache",
        "block_reason_code": kind,
        "recommended_rewrite": canned,
        "risks_flagged": [{
            "kind": kind, "severity": "block", "span": draft[:80],
        }],
        "cache_key": {
            "block_reason_code": kind,
            "jurisdiction_code": key[1],
            "product_class": key[2],
        },
    }


def cache_llm_result(
    *,
    block_reason_code: str | None,
    jurisdiction: str | None,
    product_class: str | None,
    rewrite: str | None,
) -> None:
    """Record a rewrite emitted by the LLM classifier so a future
    identical block reason gets a `source: "cache"` response.
    """
    if not block_reason_code or not rewrite:
        return
    key = _resolve_key(block_reason_code, jurisdiction, product_class)
    _runtime_cache[key] = rewrite


def first_block_kind(risks_flagged: list[dict]) -> str:
    """Extract the first `block`-severity risk's `kind`. Falls back
    to UNCLASSIFIED if none is marked block.
    """
    for r in risks_flagged or []:
        if r.get("severity") == "block" and r.get("kind"):
            return str(r["kind"])
    return UNCLASSIFIED
