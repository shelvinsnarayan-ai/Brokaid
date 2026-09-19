"""Confidence components — deterministic. Never model-reported."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Confidence:
    components: dict[str, float]
    scalar: float


def compute(
    *,
    payload: dict[str, Any],
    required_fields: list[str],
    schema_valid: bool,
    claims: list[dict[str, Any]] | None,
    proposed_class: str,
    resolved_class: str,
) -> Confidence:
    """Compute the four confidence components + min-of-components scalar.

    - schema_validity: 1.0 if Output Contract validates, else 0.0.
    - completeness: fraction of required_fields present + non-null.
    - citation_existence: fraction of claims that carry at least one citation
                          to a ledger event id or a Risk Schema field.
    - class_agreement: 1.0 if the agent's proposed class matches gAIte's
                       classification, else 0.0.
    """
    schema_validity = 1.0 if schema_valid else 0.0

    if not required_fields:
        completeness = 1.0
    else:
        present = sum(
            1 for f in required_fields
            if payload.get(f) not in (None, "", [], {})
        )
        completeness = present / len(required_fields)

    if not claims:
        citation_existence = 1.0
    else:
        with_cite = sum(
            1 for c in claims
            if isinstance(c.get("citations"), list) and len(c["citations"]) > 0
        )
        citation_existence = with_cite / len(claims)

    class_agreement = 1.0 if proposed_class == resolved_class else 0.0

    components = {
        "schema_validity": round(schema_validity, 6),
        "completeness": round(completeness, 6),
        "citation_existence": round(citation_existence, 6),
        "class_agreement": round(class_agreement, 6),
    }
    scalar = min(components.values())
    return Confidence(components=components, scalar=round(scalar, 6))
