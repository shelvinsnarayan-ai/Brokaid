"""MOCKED CarrierChannel.

FICTIONAL PRICING — NOT REAL MARKET RATES.
Deterministic per envelope_id so the same submission always produces
the same quote (RNG seeded on envelope_id).

Every response carries `mocked=True` and the ledger event that wraps
it must carry `mocked: true` too. The client-facing card must render
a MockedBadge with `label="carrier"`.
"""
from __future__ import annotations

import hashlib
import random
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


CARRIERS = ["Fictional Carrier A", "Fictional Carrier B"]


# FICTIONAL PRICING — used only to demonstrate the flow.
PRICING_RULES_MOCKED = {
    "public_liability":       {"kind": "delta_pct_of_limit", "pct": 0.05},
    "products_liability":     {"kind": "delta_pct_of_limit", "pct": 0.06},
    "professional_indemnity": {"kind": "delta_pct_of_limit", "pct": 0.04},
    "cyber_aggregate":        {"kind": "flat_new_cover_pct", "pct": 0.10},
    "marine_liability":       {"kind": "delta_pct_of_limit", "pct": 0.045},
    "employees.headcount":    {"kind": "per_head_annual", "amount": 120.0},
}


def _rng(envelope_id: str) -> random.Random:
    h = hashlib.sha256(envelope_id.encode("utf-8")).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def _find_limit_delta(requested_changes: list[dict]) -> tuple[str | None, Decimal]:
    """Return (field_path, delta) for the largest schedule-limit change."""
    best: tuple[str | None, Decimal] = (None, Decimal(0))
    for ch in requested_changes or []:
        fp = ch.get("field_path") or ""
        if "limits." not in fp and ".headcount" not in fp:
            continue
        try:
            from_v = Decimal(str(ch.get("from_value") or 0))
        except Exception:
            from_v = Decimal(0)
        try:
            to_v = Decimal(str(ch.get("to_value") or 0))
        except Exception:
            to_v = Decimal(0)
        delta = to_v - from_v
        if abs(delta) > abs(best[1]):
            best = (fp, delta)
    return best


def _rule_key_for(field_path: str, cover_type: str | None) -> str:
    if ".headcount" in field_path:
        return "employees.headcount"
    if cover_type and cover_type in PRICING_RULES_MOCKED:
        return cover_type
    # Fall back on last dotted segment.
    for known in PRICING_RULES_MOCKED:
        if field_path.endswith(known.split(".")[-1]):
            return known
    return "public_liability"


async def submit_endorsement(
    *,
    envelope_id: str,
    submission: dict,
    policy_current: dict,
) -> dict[str, Any]:
    """Simulate a carrier response. Deterministic per envelope_id."""
    rng = _rng(envelope_id)
    ss = submission.get("structured_submission") or submission
    cover_type = ss.get("cover_type")
    requested_changes = ss.get("requested_changes") or submission.get("requested_changes") or []
    field_path, delta = _find_limit_delta(requested_changes)
    rule_key = _rule_key_for(field_path or "", cover_type)
    rule = PRICING_RULES_MOCKED.get(rule_key, PRICING_RULES_MOCKED["public_liability"])

    if rule["kind"] == "delta_pct_of_limit":
        delta_amt = (delta * Decimal(str(rule["pct"]))).quantize(Decimal("0.01"))
    elif rule["kind"] == "per_head_annual":
        delta_amt = (delta * Decimal(str(rule["amount"]))).quantize(Decimal("0.01"))
    elif rule["kind"] == "flat_new_cover_pct":
        existing = Decimal(str(policy_current.get("premium_amount") or 0))
        delta_amt = (existing * Decimal(str(rule["pct"]))).quantize(Decimal("0.01"))
    else:
        delta_amt = Decimal(0)

    current_premium = Decimal(str(policy_current.get("premium_amount") or 0))
    new_annual = (current_premium + delta_amt).quantize(Decimal("0.01"))
    currency = policy_current.get("premium_currency") or "AUD"

    carrier = CARRIERS[rng.randint(0, len(CARRIERS) - 1)]

    conditions = [
        f"Certificate must be issued within {rng.randint(20, 40)} days of bind.",
        "Continuous cover confirmed — no gap since last renewal.",
    ]
    terms = (
        f"Quote valid for {rng.randint(21, 45)} days. "
        f"Subject to executed endorsement wording."
    )

    quote_id = str(uuid.UUID(bytes=hashlib.sha256(
        (envelope_id + carrier).encode("utf-8")
    ).digest()[:16]))

    return {
        "quote_id": quote_id,
        "carrier_name": carrier,
        "premium_change_amount": str(delta_amt),
        "premium_new_annual": str(new_annual),
        "premium_currency": currency,
        "terms": terms,
        "conditions": conditions,
        "quoted_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "mocked": True,
    }
