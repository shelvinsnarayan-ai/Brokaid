"""clAIms stub — deterministic FNOL triage.

Phase-13 build-1 posture: the claim scene needs a triage seam but we
have no LLM budget for it (and the user opted for a stub over live).
This module implements a rule-based classifier + fact extractor that
implements the `AgentProvider` protocol so brAIn's orchestration
(AgentInvocationStarted / AgentOutputProduced / …Completed events,
sprint-usage projection) is exercised end-to-end.

No LLM call. `tokens_used = (0, 0)`. `wall_ms` is the actual wall
time of the rule pass.
"""
from __future__ import annotations

import re
import time
from typing import Any

from app.kernel.contracts.models import ContextEnvelope, OutputContract, TokenCount


CLAIMS_STUB_ELIGIBLE_CLASSES = frozenset({"claim_admin"})


CLAIM_TYPE_RULES: list[tuple[str, re.Pattern]] = [
    ("cyber",                 re.compile(r"\b(ransomware|malware|phishing|breach|cyber|data\s+leak|hacked?|encrypted)\b", re.I)),
    ("marine",                re.compile(r"\b(cargo|shipment|vessel|maritime|marine|freight|container)\b", re.I)),
    ("professional_indemnity",re.compile(r"\b(negligen(?:ce|t)|advice|breach\s+of\s+duty|malpractice|professional)\b", re.I)),
    ("liability",             re.compile(r"\b(injur(?:ed|y)|slip|fell|trip|third[-\s]party|liab(?:le|ility)|damage\s+to\s+.+'s)\b", re.I)),
    ("property_damage",       re.compile(r"\b(fire|flood(?:ed|ing)?|storm|hail|burst|leak|damage|water|smoke|broke(?:n)?|shatter)\b", re.I)),
]

URGENCY_RULES: list[tuple[str, re.Pattern]] = [
    ("critical", re.compile(r"\b(life[-\s]threatening|serious\s+injury|hospital|fatality|died|death|urgent|emergency|business\s+halted|shut\s+down)\b", re.I)),
    ("high",     re.compile(r"\b(burst|flooding?|major|significant|extensive|large|entire|whole)\b", re.I)),
]

COVERAGE_QUESTION_RULES: list[re.Pattern] = [
    re.compile(r"\b(are\s+we\s+covered|is\s+this\s+covered|will\s+(?:you|the\s+insurer)\s+pay|does\s+(?:my|our)\s+policy\s+cover)\b", re.I),
    re.compile(r"\bcoverage\s+question\b", re.I),
]

DATE_RE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b")
AMOUNT_RE = re.compile(r"\b(?:AUD|USD|\$|€|£)\s?[\d,]+(?:\.\d{2})?\b", re.I)
ADDRESS_HINT_RE = re.compile(r"\b(?:at|near|address)\s+([\w\s\d,\.-]{5,80})", re.I)


def classify(raw_fnol_message: str, matter_kind: str = "claim") -> dict[str, Any]:
    """Deterministic triage. Returns the phase-13 payload shape."""
    text = (raw_fnol_message or "").strip()
    if not text:
        return {
            "claim_type": "general",
            "urgency": "normal",
            "key_facts": [],
            "requires_coverage_check": False,
            "structured_summary": "(empty message)",
        }

    # Claim type: first matching rule wins; falls back to 'general'.
    claim_type = "general"
    for kind, pat in CLAIM_TYPE_RULES:
        if pat.search(text):
            claim_type = kind
            break

    # Urgency: highest matching bucket wins.
    urgency = "normal"
    for level, pat in URGENCY_RULES:
        if pat.search(text):
            urgency = level
            break

    # Key facts.
    facts: list[str] = []
    for m in DATE_RE.finditer(text):
        facts.append(f"date_mention: {m.group(0)}")
    for m in AMOUNT_RE.finditer(text):
        facts.append(f"amount_mention: {m.group(0)}")
    m = ADDRESS_HINT_RE.search(text)
    if m:
        facts.append(f"location_hint: {m.group(1).strip()}")
    facts = facts[:8]

    # Coverage question detection routes toward HITL.
    coverage_q = any(pat.search(text) for pat in COVERAGE_QUESTION_RULES)

    # Deterministic structured summary. No LLM required.
    line_1 = f"Claim type: {claim_type.replace('_', ' ')} · urgency: {urgency}."
    line_2 = f"Message: {text[:200]}{'…' if len(text) > 200 else ''}"
    line_3 = (
        "Client asked about coverage." if coverage_q
        else "No coverage question detected."
    )
    structured_summary = "\n".join([line_1, line_2, line_3])

    return {
        "claim_type": claim_type,
        "urgency": urgency,
        "key_facts": facts,
        "requires_coverage_check": coverage_q,
        "structured_summary": structured_summary,
    }


class AgentClassMismatch(Exception):
    """claims_stub was asked to run on a class it does not handle."""


class DeterministicClaimsStubProvider:
    """AgentProvider seam for the claims stub — brAIn-compatible."""

    agent_name = "claims"

    async def run(self, envelope: ContextEnvelope) -> OutputContract:
        if envelope.class_key not in CLAIMS_STUB_ELIGIBLE_CLASSES:
            raise AgentClassMismatch(
                f"claims stub does not handle class {envelope.class_key!r}"
            )
        inputs = envelope.inputs or {}
        start = time.monotonic()
        triage = classify(
            raw_fnol_message=inputs.get("raw_fnol_message") or "",
            matter_kind=inputs.get("matter_kind") or "claim",
        )
        wall_ms = int((time.monotonic() - start) * 1000)

        return OutputContract(
            envelope_id=envelope.envelope_id,
            class_key=envelope.class_key,
            decision="propose-triage",
            payload=triage,
            citations=[],
            notes="claims deterministic stub",
            tokens_used=TokenCount(input=0, output=0),
            wall_ms=wall_ms,
            provider_meta={
                "mocked": False,
                "stub": True,
                "model_id": None,
                "provider": "deterministic",
            },
        )
