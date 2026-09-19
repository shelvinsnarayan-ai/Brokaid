"""Property-based invariants over the gAIte class registry."""
from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from app.kernel.gate.classes import CLASSES, H_CLASSES
from app.kernel.gate.confidence import Confidence
from app.kernel.gate.gate import evaluate


def _conf(v: float) -> Confidence:
    return Confidence(
        components={
            "schema_validity": v, "completeness": v,
            "citation_existence": v, "class_agreement": v,
        },
        scalar=v,
    )


# Reasonable set of ctx flags used across the taxonomy.
_FLAG_KEYS = [
    "tmd_answered", "tmd_consistent", "recorded", "tmd_inconsistent",
    "tmd_unanswered", "consent_on_file", "existing_client", "templated",
    "new_client", "vulnerable_flag", "renewal", "compliance_pass",
    "new_business", "non_standard_class", "factual",
    "sourced_from_risk_schema", "judgement_answer", "client_instructed",
    "client_paid", "insurer_quoted", "rerate", "decline", "conditions",
    "ambiguous_reply", "after_human_bind", "on_approved_list",
    "off_list_change", "policy_in_force", "compliant",
    "non_standard_wording", "open_complaint", "any_term_change",
    "coverage_language_detected",
]


ctx_strategy = st.fixed_dictionaries({
    **{k: st.booleans() for k in _FLAG_KEYS},
    "est_premium": st.floats(min_value=0, max_value=1e12, allow_nan=False, allow_infinity=False),
    "doc_qa_conf": st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
})
conf_strategy = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)
tcfg_strategy = st.fixed_dictionaries({
    "submit_to_market_threshold": st.floats(min_value=0, max_value=1e12, allow_nan=False, allow_infinity=False),
})


# ---- 1. H invariant ---------------------------------------------------------

@pytest.mark.parametrize("class_key", sorted(H_CLASSES))
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(ctx=ctx_strategy, conf=conf_strategy, tcfg=tcfg_strategy)
def test_h_invariant_never_autonomous(class_key, ctx, conf, tcfg):
    dec = evaluate(class_key, ctx, _conf(conf), tcfg)
    assert dec.decision in ("HITL", "BLOCK"), (
        f"H class {class_key} produced AUTONOMOUS_OK for ctx={ctx} conf={conf}"
    )


# ---- 2. Autonomy classes: reachable both HITL and AUTONOMOUS_OK -------------

@pytest.mark.parametrize(
    "class_key",
    sorted(k for k, c in CLASSES.items() if not c.never_autonomous and k != "complaint_clock"),
)
def test_autonomy_class_is_reachable(class_key):
    # negotiate_send, cancel_refund have autonomy_predicate = false and hitl = true.
    if class_key in ("negotiate_send", "cancel_refund"):
        pytest.skip(f"{class_key} is always HITL by design (non-H)")

    # Try a spread of inputs to confirm at least one AUTONOMOUS_OK exists.
    hi_ctx = {k: True for k in _FLAG_KEYS}
    hi_ctx.update({
        "tmd_inconsistent": False, "tmd_unanswered": False,
        "new_client": False, "vulnerable_flag": False,
        "new_business": False, "non_standard_class": False,
        "judgement_answer": False,
        "rerate": False, "decline": False, "conditions": False,
        "ambiguous_reply": False, "off_list_change": False,
        "non_standard_wording": False, "open_complaint": False,
        "any_term_change": False, "coverage_language_detected": False,
        "est_premium": 100.0, "doc_qa_conf": 1.0,
    })
    tcfg = {"submit_to_market_threshold": 1_000_000.0}

    d_auto = evaluate(class_key, hi_ctx, _conf(1.0), tcfg)
    d_hitl = evaluate(class_key, {k: False for k in _FLAG_KEYS} | {"est_premium": 1e11, "doc_qa_conf": 0.0}, _conf(0.0), tcfg)
    assert d_auto.decision == "AUTONOMOUS_OK", (
        f"{class_key}: full-flags high-conf did not produce AUTONOMOUS_OK "
        f"(got {d_auto.decision}: {d_auto.reason})"
    )
    assert d_hitl.decision == "HITL"


def test_complaint_clock_is_always_autonomous_non_H():
    tcfg = {"submit_to_market_threshold": 1e6}
    d = evaluate("complaint_clock", {}, _conf(0.0), tcfg)
    assert d.decision == "AUTONOMOUS_OK"


# ---- 3. Confidence components always present -------------------------------

def test_confidence_components_present_on_every_decision():
    tcfg = {"submit_to_market_threshold": 1e6}
    for key in CLASSES:
        d = evaluate(key, {}, _conf(0.5), tcfg)
        assert set(d.confidence.components.keys()) == {
            "schema_validity", "completeness", "citation_existence", "class_agreement"
        }
        assert 0.0 <= d.confidence.scalar <= 1.0
        assert d.class_key == key


# ---- 4. Unlock protocol gates ---------------------------------------------

def test_unlock_protocol_gates_autonomous():
    tcfg = {"submit_to_market_threshold": 1e6}
    # A class that would otherwise autonomously pass.
    ctx = {"tmd_answered": True, "tmd_consistent": True, "recorded": True}
    # unlocked=True → AUTONOMOUS_OK; unlocked=False → HITL "class not unlocked"
    d_ok = evaluate("ddo_tmd_check", ctx, _conf(1.0), tcfg, unlock_lookup=lambda k: True)
    d_locked = evaluate("ddo_tmd_check", ctx, _conf(1.0), tcfg, unlock_lookup=lambda k: False)
    assert d_ok.decision == "AUTONOMOUS_OK"
    assert d_locked.decision == "HITL"
    assert d_locked.reason == "class not unlocked"
