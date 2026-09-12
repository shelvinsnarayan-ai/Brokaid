"""Action-class taxonomy — encoded verbatim from the phase-2 brief §7.3.

The registry is data, not a switch. Every class is introspectable and
testable. Predicates are pure functions of `(ctx, conf, tcfg)` where:
  ctx  = dict of context flags/values for the specific action
  conf = float scalar confidence 0..1 (min-of-components)
  tcfg = tenant_config dict (thresholds, per-tenant tuning)

Do NOT hardcode monetary numbers — `value` in each class points at
a tenant_config key.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


Predicate = Callable[[dict, float, dict], bool]


@dataclass(frozen=True)
class ClassDef:
    key: str
    reversible: str            # 'yes' | 'no' | 'partly'
    value: str | None          # tenant_config key, or None
    never_autonomous: bool
    autonomy_predicate: Predicate
    hitl_predicate: Predicate
    unlock_state_key: str = field(default="")


def _false(_ctx, _conf, _tcfg): return False
def _true(_ctx, _conf, _tcfg): return True


CLASSES: dict[str, ClassDef] = {}


def _register(**kw) -> None:
    if not kw.get("unlock_state_key"):
        kw["unlock_state_key"] = kw["key"]
    CLASSES[kw["key"]] = ClassDef(**kw)


# --- structure_risk ------------------------------------------------------
_register(
    key="structure_risk", reversible="yes", value=None, never_autonomous=False,
    autonomy_predicate=lambda ctx, conf, tcfg: conf >= 0.85,
    hitl_predicate=lambda ctx, conf, tcfg: conf < 0.85,
)

# --- ddo_tmd_check -------------------------------------------------------
_register(
    key="ddo_tmd_check", reversible="yes", value=None, never_autonomous=False,
    autonomy_predicate=lambda ctx, conf, tcfg: (
        bool(ctx.get("tmd_answered"))
        and bool(ctx.get("tmd_consistent"))
        and bool(ctx.get("recorded"))
    ),
    hitl_predicate=lambda ctx, conf, tcfg: (
        bool(ctx.get("tmd_inconsistent")) or bool(ctx.get("tmd_unanswered"))
    ),
)

# --- edelivery_consent ---------------------------------------------------
_register(
    key="edelivery_consent", reversible="yes", value=None, never_autonomous=False,
    autonomy_predicate=lambda ctx, conf, tcfg: bool(ctx.get("consent_on_file")),
    hitl_predicate=lambda ctx, conf, tcfg: not bool(ctx.get("consent_on_file")),
)

# --- request_info --------------------------------------------------------
_register(
    key="request_info", reversible="yes", value=None, never_autonomous=False,
    autonomy_predicate=lambda ctx, conf, tcfg: (
        bool(ctx.get("existing_client"))
        and bool(ctx.get("templated"))
        and conf >= 0.85
    ),
    hitl_predicate=lambda ctx, conf, tcfg: (
        bool(ctx.get("new_client")) or bool(ctx.get("vulnerable_flag"))
    ),
)

# --- submit_to_market ----------------------------------------------------
_register(
    key="submit_to_market", reversible="partly", value="submit_to_market_threshold",
    never_autonomous=False,
    autonomy_predicate=lambda ctx, conf, tcfg: (
        bool(ctx.get("renewal"))
        and bool(ctx.get("existing_client"))
        and float(ctx.get("est_premium", 0)) <= float(tcfg.get("submit_to_market_threshold", 0))
        and conf >= 0.85
        and bool(ctx.get("compliance_pass"))
    ),
    hitl_predicate=lambda ctx, conf, tcfg: (
        bool(ctx.get("new_business"))
        or float(ctx.get("est_premium", 0)) > float(tcfg.get("submit_to_market_threshold", 0))
        or bool(ctx.get("non_standard_class"))
    ),
)

# --- underwriter_qa ------------------------------------------------------
_register(
    key="underwriter_qa", reversible="yes", value=None, never_autonomous=False,
    autonomy_predicate=lambda ctx, conf, tcfg: (
        bool(ctx.get("factual")) and bool(ctx.get("sourced_from_risk_schema"))
    ),
    hitl_predicate=lambda ctx, conf, tcfg: bool(ctx.get("judgement_answer")),
)

# --- negotiate_send ------------------------------------------------------
_register(
    key="negotiate_send", reversible="yes", value=None, never_autonomous=False,
    autonomy_predicate=_false,
    hitl_predicate=_true,
)

# --- bind_new_or_remarket (H) --------------------------------------------
_register(
    key="bind_new_or_remarket", reversible="no", value=None, never_autonomous=True,
    autonomy_predicate=_false,
    hitl_predicate=_true,
)

# --- endorsement_execute_instructed_quoted -------------------------------
_register(
    key="endorsement_execute_instructed_quoted", reversible="partly", value=None,
    never_autonomous=False,
    autonomy_predicate=lambda ctx, conf, tcfg: (
        bool(ctx.get("client_instructed"))
        and bool(ctx.get("client_paid"))
        and bool(ctx.get("insurer_quoted"))
        and bool(ctx.get("compliance_pass"))
        and conf >= 0.9
    ),
    hitl_predicate=lambda ctx, conf, tcfg: (
        bool(ctx.get("rerate"))
        or bool(ctx.get("decline"))
        or bool(ctx.get("conditions"))
        or bool(ctx.get("ambiguous_reply"))
    ),
)

# --- issue_documents -----------------------------------------------------
_register(
    key="issue_documents", reversible="partly", value=None, never_autonomous=False,
    autonomy_predicate=lambda ctx, conf, tcfg: (
        bool(ctx.get("after_human_bind"))
        and float(ctx.get("doc_qa_conf", 0)) >= 0.95
    ),
    hitl_predicate=lambda ctx, conf, tcfg: float(ctx.get("doc_qa_conf", 0)) < 0.95,
)

# --- endorsement_standard ------------------------------------------------
_register(
    key="endorsement_standard", reversible="yes", value=None, never_autonomous=False,
    autonomy_predicate=lambda ctx, conf, tcfg: (
        bool(ctx.get("on_approved_list"))
        and conf >= 0.85
        and bool(ctx.get("compliance_pass"))
    ),
    hitl_predicate=lambda ctx, conf, tcfg: bool(ctx.get("off_list_change")),
)

# --- cancel_refund -------------------------------------------------------
_register(
    key="cancel_refund", reversible="no", value=None, never_autonomous=False,
    autonomy_predicate=_false,
    hitl_predicate=_true,
)

# --- certificate_issue ---------------------------------------------------
_register(
    key="certificate_issue", reversible="yes", value=None, never_autonomous=False,
    autonomy_predicate=lambda ctx, conf, tcfg: (
        bool(ctx.get("policy_in_force"))
        and bool(ctx.get("compliant"))
        and conf >= 0.9
    ),
    hitl_predicate=lambda ctx, conf, tcfg: bool(ctx.get("non_standard_wording")),
)

# --- comms_service -------------------------------------------------------
_register(
    key="comms_service", reversible="yes", value=None, never_autonomous=False,
    autonomy_predicate=lambda ctx, conf, tcfg: conf >= 0.85,
    hitl_predicate=lambda ctx, conf, tcfg: (
        bool(ctx.get("vulnerable_flag")) or bool(ctx.get("open_complaint"))
    ),
)

# --- comms_advisory (H) --------------------------------------------------
_register(
    key="comms_advisory", reversible="no", value=None, never_autonomous=True,
    autonomy_predicate=_false,
    hitl_predicate=_true,
)

# --- renewal_pack_unchanged ----------------------------------------------
_register(
    key="renewal_pack_unchanged", reversible="yes", value=None, never_autonomous=False,
    autonomy_predicate=lambda ctx, conf, tcfg: (
        conf >= 0.85 and bool(ctx.get("compliance_pass"))
    ),
    hitl_predicate=lambda ctx, conf, tcfg: bool(ctx.get("any_term_change")),
)

# --- claim_admin ---------------------------------------------------------
_register(
    key="claim_admin", reversible="yes", value=None, never_autonomous=False,
    autonomy_predicate=lambda ctx, conf, tcfg: (
        conf >= 0.85 and not bool(ctx.get("coverage_language_detected"))
    ),
    hitl_predicate=lambda ctx, conf, tcfg: bool(ctx.get("coverage_language_detected")),
)

# --- coverage_position (H) -----------------------------------------------
_register(
    key="coverage_position", reversible="no", value=None, never_autonomous=True,
    autonomy_predicate=_false,
    hitl_predicate=_true,
)

# --- trust_money (H) -----------------------------------------------------
_register(
    key="trust_money", reversible="no", value=None, never_autonomous=True,
    autonomy_predicate=_false,
    hitl_predicate=_true,
)

# --- complaint_clock -----------------------------------------------------
_register(
    key="complaint_clock", reversible="yes", value=None, never_autonomous=False,
    autonomy_predicate=_true,      # statutory — always autonomous
    hitl_predicate=_false,
)

# --- complaint_determination (H) -----------------------------------------
_register(
    key="complaint_determination", reversible="no", value=None, never_autonomous=True,
    autonomy_predicate=_false,
    hitl_predicate=_true,
)

# --- clear_block (H) -----------------------------------------------------
_register(
    key="clear_block", reversible="no", value=None, never_autonomous=True,
    autonomy_predicate=_false,
    hitl_predicate=_true,
)


H_CLASSES = {k for k, c in CLASSES.items() if c.never_autonomous}


def get(class_key: str) -> ClassDef:
    if class_key not in CLASSES:
        raise KeyError(f"unknown class: {class_key}")
    return CLASSES[class_key]


def all_keys() -> list[str]:
    return list(CLASSES.keys())
