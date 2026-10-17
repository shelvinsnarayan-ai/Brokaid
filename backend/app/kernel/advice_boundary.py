"""Thin F/G/P/X advice-boundary classifier (R0.3). Rules only, no LLM.

F = factual data update · G = guided (artefact the client already knows
they want) · P = personal advice (broker) · X = out of scope.
Limit / sum-insured / excess changes are always P (A4-4).
"""
from __future__ import annotations

import re
from typing import Any

from app.kernel.policy_field_guard import UnmatchedPolicyField, validate_requested_changes

_ADVICE = re.compile(
    r"\b(should (i|we)|should \w+ (be|get|have|go)|do (i|we) need|would you recommend|recommend|advi[cs]e|"
    r"is it (better|worth)|worth it|suitab\w*|what do you think|which (is|one|option)|"
    r"better off|or (should|would|do) (i|we))\b", re.I)
_TEXT_KINDS = [
    ("limit", re.compile(r"\b(limit|sum insured|cover amount|excess|indemnity|increase (my |the )?cover)\b", re.I)),
    ("certificate", re.compile(r"\b(certificate|coc|proof of (insurance|cover)|currency)\b", re.I)),
    ("driver", re.compile(r"\bdrivers?\b", re.I)),
    ("vehicle", re.compile(r"\b(vehicles?|car|ute|truck|van|rego|registration|fleet|hilux|navara)\b", re.I)),
    ("address", re.compile(r"\b(address|moved|moving|relocat\w*|garaged)\b", re.I)),
]


def _kind(text: str, paths: list[str]) -> str | None:
    for kind, marker in (("limit", "limits."), ("address", "address"),
                         ("driver", "schedule.drivers"), ("vehicle", "schedule.vehicles")):
        if any(marker in p for p in paths):
            return kind
    for kind, rx in _TEXT_KINDS:
        if rx.search(text):
            return kind
    return None


def _v(tier: str, rule_id: str, reason: str, kind: str | None) -> dict[str, Any]:
    return {"tier": tier, "rule_id": rule_id, "reason": reason, "request_type": kind}


_PERSONAL = re.compile(
    r"\b(your (business|needs|circumstances|situation|risks?|operations|turnover|fleet|cash ?flow|budget|claims?)|"
    r"suits? you|for you|you should|we (recommend|advise) (that )?you|in your case|given (that )?you|because you)\b", re.I)


def classify_recommendation(text: str, quote: dict | None, expiring: dict | None) -> dict[str, Any]:
    """R1.6 — a broker's renewal recommendation. P (personal advice) if it weighs the client's own
    circumstances, or the chosen option changes a limit or the excess (A4-4); otherwise G (general comparison)."""
    sched = (expiring or {}).get("schedule") or {}
    q = quote or {}
    old_exc = (sched.get("excess") or {}).get("any_one_occurrence")
    if q.get("limits") and q["limits"] != (sched.get("limits") or {}):
        return _v("P", "RENEWAL-P-02", "The recommended option changes a limit (A4-4)", "renewal")
    if q.get("excess") is not None and old_exc is not None and float(q["excess"]) != float(old_exc):
        return _v("P", "RENEWAL-P-02", "The recommended option changes the excess (A4-4)", "renewal")
    if _PERSONAL.search(text or ""):
        return _v("P", "RENEWAL-P-01", "The recommendation weighs the client's own circumstances", "renewal")
    return _v("G", "RENEWAL-G-01", "General comparison of renewal terms (facts only)", "renewal")


def classify(raw_message: str, structured_submission: dict | None, policy: dict | None) -> dict[str, Any]:
    text = raw_message or ""
    changes = (structured_submission or {}).get("requested_changes") or []
    kind = _kind(text, [c.get("field_path") or "" for c in changes])
    advice = bool(_ADVICE.search(text))

    if kind == "limit":
        return _v("P", "LIMIT-P-01", "Limit, sum-insured or excess change needs broker advice (A4-4)", kind)
    if kind == "certificate":
        return _v("G", "CERT-G-01", "Certificate request — produce the artefact the client asked for", kind)
    if kind in ("vehicle", "driver", "address"):
        if advice:
            return _v("P", f"{kind.upper()}-P-01", f"Client is asking for advice about a {kind} change", kind)
        if kind == "vehicle":
            try:
                validate_requested_changes(policy or {}, changes)
            except UnmatchedPolicyField:
                return _v("P", "VEHICLE-P-02", "Vehicle reference doesn't match the policy", kind)
        if kind == "driver":
            adds = [c for c in changes if (c.get("operation") or "").lower() == "add"]
            if any(not (isinstance(c.get("to_value"), dict) and c["to_value"].get("name")
                        and (c["to_value"].get("licence_number") or c["to_value"].get("licence")))
                   for c in adds):
                return _v("G", "DRIVER-G-01", "Driver add is missing details — broker guides the client", kind)
        return _v("F", f"{kind.upper()}-F-01", f"Factual {kind} update on the policy schedule", kind)
    return _v("X", "UNCLASSIFIED-X-01", "unclassified request type — routed to broker", None)
