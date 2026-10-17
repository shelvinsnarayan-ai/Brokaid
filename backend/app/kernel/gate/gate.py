"""gAIte.evaluate() — the central deterministic gate."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.kernel.gate.classes import ClassDef, get
from app.kernel.gate.confidence import Confidence
from app.kernel.gate.coverage_guard import check as coverage_check


Decision = str  # "AUTONOMOUS_OK" | "HITL" | "BLOCK"


@dataclass(frozen=True)
class GateDecision:
    decision: Decision
    class_key: str
    confidence: Confidence
    reason: str
    applied_rules: list[str]
    blocked_spans: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "class_key": self.class_key,
            "confidence": {
                "components": dict(self.confidence.components),
                "scalar": self.confidence.scalar,
            },
            "reason": self.reason,
            "applied_rules": list(self.applied_rules),
            "blocked_spans": list(self.blocked_spans),
        }


def evaluate(
    class_key: str,
    ctx: dict,
    confidence: Confidence,
    tenant_config: dict,
    unlock_lookup: Callable[[str], bool] | None = None,
    compliance_block_id: str | None = None,
) -> GateDecision:
    """Evaluate one action. Deterministic; zero side effects.

    Decision rule (in this exact order):
      0. compliance_block_id set → BLOCK (`compliance_block:<id>`)
      1. never_autonomous → HITL
      2. hitl_predicate    → HITL (reason)
      3. class not unlocked → HITL ("class not unlocked")
      4. autonomy_predicate → AUTONOMOUS_OK
      5. fall-through → HITL ("no autonomy predicate matched")
    """
    cls: ClassDef = get(class_key)
    conf = confidence.scalar
    applied: list[str] = []

    # 0. Compliance block — takes precedence over everything else.
    if compliance_block_id:
        applied.append("compliance_block")
        return GateDecision(
            decision="BLOCK", class_key=class_key,
            confidence=confidence,
            reason=f"compliance_block:{compliance_block_id}",
            applied_rules=applied,
        )

    # 1. H invariant
    if cls.never_autonomous:
        applied.append("never_autonomous")
        return GateDecision(
            decision="HITL", class_key=class_key,
            confidence=confidence, reason="class marked H (never_autonomous)",
            applied_rules=applied,
        )

    # 2. HITL predicate
    try:
        hitl_hit = bool(cls.hitl_predicate(ctx, conf, tenant_config))
    except Exception as e:
        applied.append("hitl_predicate_error")
        return GateDecision(
            decision="HITL", class_key=class_key,
            confidence=confidence, reason=f"hitl_predicate error: {e!r}",
            applied_rules=applied,
        )
    if hitl_hit:
        applied.append("hitl_predicate")
        return GateDecision(
            decision="HITL", class_key=class_key,
            confidence=confidence, reason="hitl_predicate matched",
            applied_rules=applied,
        )

    # 3. Unlock protocol
    if unlock_lookup is not None:
        unlocked = bool(unlock_lookup(class_key))
        if not unlocked:
            applied.append("class_not_unlocked")
            return GateDecision(
                decision="HITL", class_key=class_key,
                confidence=confidence, reason="class not unlocked",
                applied_rules=applied,
            )

    # 4. Autonomy predicate
    try:
        auto_hit = bool(cls.autonomy_predicate(ctx, conf, tenant_config))
    except Exception as e:
        applied.append("autonomy_predicate_error")
        return GateDecision(
            decision="HITL", class_key=class_key,
            confidence=confidence, reason=f"autonomy_predicate error: {e!r}",
            applied_rules=applied,
        )
    if auto_hit:
        applied.append("autonomy_predicate")
        return GateDecision(
            decision="AUTONOMOUS_OK", class_key=class_key,
            confidence=confidence, reason="autonomy_predicate matched",
            applied_rules=applied,
        )

    # 5. Fall-through — never AUTONOMOUS_OK
    applied.append("fall_through")
    return GateDecision(
        decision="HITL", class_key=class_key,
        confidence=confidence, reason="no autonomy predicate matched",
        applied_rules=applied,
    )


def check_client_message(text: str) -> dict:
    """Coverage-language post-check (BLOCK if hit)."""
    result = coverage_check(text)
    if not result["passed"]:
        return {
            "decision": "BLOCK",
            "reason": result["reason"],
            "blocked_spans": result["blocked_spans"],
        }
    return {"decision": "PASS", "reason": None, "blocked_spans": []}
