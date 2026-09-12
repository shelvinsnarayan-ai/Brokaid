"""Coverage-language guard.

Deterministic post-check applied to any client-facing message in a
claim context. Blocks sentences that imply a coverage position without
a human sign-off. Zero model calls.

Return: `{passed: bool, blocked_spans: [{rule_id, start, end, text}], reason}`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern
    description: str


# Direct coverage-position phrases. Case-insensitive. \b anchors so
# "recovered" / "uncovered" don't false-fire.
_RULES: list[Rule] = [
    Rule("covered_direct",
         re.compile(r"\b(?:you(?:'re| are)|this is|it is|it's)\s+covered\b", re.IGNORECASE),
         "direct 'you are covered' assertion"),
    Rule("coverage_applies",
         re.compile(r"\bcoverage\s+applies\b", re.IGNORECASE),
         "'coverage applies' assertion"),
    Rule("will_pay",
         re.compile(r"\b(?:we|insurer|the insurer|the claim)\s+(?:will\s+)?(?:be\s+)?(?:pay(?:s|ing)?|paid)\b", re.IGNORECASE),
         "'will pay' promise"),
    Rule("we_will_pay_short",
         re.compile(r"\bwe['`]ll\s+pay\b", re.IGNORECASE),
         "'we'll pay' promise"),
    Rule("not_covered",
         re.compile(r"\b(?:not covered|no coverage|coverage (?:is )?denied)\b", re.IGNORECASE),
         "'not covered' denial"),
    Rule("declined_coverage",
         # 'declined' as a coverage decision — narrower than any 'declined'
         re.compile(r"\b(?:claim|cover(?:age)?)\s+(?:is|has been|was)?\s*declined\b", re.IGNORECASE),
         "'claim declined' or 'coverage declined'"),
    Rule("policy_responds",
         re.compile(r"\bpolicy\s+(?:does\s+not\s+)?respond(?:s)?\b", re.IGNORECASE),
         "'policy responds / does not respond'"),
    Rule("excluded_under",
         re.compile(r"\bexcluded\s+under\b", re.IGNORECASE),
         "'excluded under [clause]'"),
    Rule("falls_within_exclusion",
         re.compile(r"\bfalls\s+within\s+(?:the\s+)?exclusion\b", re.IGNORECASE),
         "'falls within the exclusion'"),
]


# Compound rule: sentence contains BOTH a policy noun AND a decision verb
# in the same clause, unless the decision is punted (e.g. "your broker will").
_POLICY_NOUNS = r"\b(?:polic(?:y|ies)|cover(?:age)?|insurer|wording)\b"
_DECISION_VERBS = r"\b(?:approve[ds]?|deny(?:ing)?|denied|pay(?:s|ing|ed)?|paid|cover(?:s|ing|ed)?|exclude[sd]?)\b"
_PUNT_PREFIX = re.compile(
    r"\b(?:i(?:'ve| have)?\s+asked|your\s+(?:broker|adviser)\s+will|we\s+(?:will\s+)?(?:need|ask)\s+to)\b",
    re.IGNORECASE,
)
_POLICY_RE = re.compile(_POLICY_NOUNS, re.IGNORECASE)
_VERB_RE = re.compile(_DECISION_VERBS, re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"[.!?]+")


def _compound_hit(text: str) -> bool:
    if _PUNT_PREFIX.search(text):
        return False
    return bool(_POLICY_RE.search(text) and _VERB_RE.search(text))


def check(text: str) -> dict[str, Any]:
    """Return `{passed, blocked_spans, reason}`.

    Sentence-level pipeline:
      * split into sentences,
      * if a sentence starts with a punt prefix ("I've asked the insurer…",
        "your broker will…", "we will need to…") — skip all rules for it,
      * otherwise apply each direct rule + the compound policy-noun /
        decision-verb rule.
    """
    if not text:
        return {"passed": True, "blocked_spans": [], "reason": None}

    blocked: list[dict[str, Any]] = []
    offset = 0
    for sentence in _SENTENCE_SPLIT.split(text):
        length = len(sentence)
        stripped = sentence.strip()
        if not stripped:
            offset += length + 1
            continue
        sub_start = text.find(stripped, offset)
        if sub_start < 0:
            sub_start = offset

        # Punted sentences carry no assertion — allow-list.
        if _PUNT_PREFIX.search(stripped):
            offset += length + 1
            continue

        # Direct rules
        for rule in _RULES:
            for m in rule.pattern.finditer(stripped):
                blocked.append({
                    "rule_id": rule.id,
                    "start": sub_start + m.start(),
                    "end": sub_start + m.end(),
                    "text": m.group(0),
                })

        # Compound rule
        if _compound_hit(stripped):
            blocked.append({
                "rule_id": "policy_noun_decision_verb_bind",
                "start": sub_start,
                "end": sub_start + len(stripped),
                "text": stripped,
            })

        offset += length + 1

    if blocked:
        blocked.sort(key=lambda b: (b["start"], b["end"]))
        return {
            "passed": False,
            "blocked_spans": blocked,
            "reason": f"coverage-language detected ({len(blocked)} rule hit(s))",
        }
    return {"passed": True, "blocked_spans": [], "reason": None}
