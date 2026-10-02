"""MOCKED CarrierChannel.

FICTIONAL PRICING — NOT REAL MARKET RATES.
Deterministic per envelope_id so the same submission always produces
the same quote (RNG seeded on envelope_id).

R1 close-out (user prod verification 2026-10-02):
  · Pricing is now a ratio-of-base-premium so a $10M→$30M PL uplift on
    an $8,500 policy quotes a plausible ~$2,550 — not the $1.35M
    nonsense that came out of the old `delta_pct_of_limit` rule.
  · Carrier identity is READ FROM THE POLICY (`insurer_display`), not
    randomised. One policy = one carrier end-to-end.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


# k = elasticity of premium to limit change. Tuned so that for a
# doubling of the limit (ratio=2) the premium rises by ~15% of base.
# Capped at 1.0× base so no mocked quote can ever balloon into the
# user's reported pathology.
LIMIT_ELASTICITY_K = Decimal("0.15")
PREMIUM_CHANGE_CAP_MULT = Decimal("1.0")

# Per-headcount uplift kept as before (not a limit path).
PER_HEAD_ANNUAL = Decimal("120.00")


def _dec(v) -> Decimal:
    if v is None:
        return Decimal(0)
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal(0)


def _ratio_based_change(
    base_premium: Decimal, from_limit: Decimal, to_limit: Decimal,
) -> Decimal:
    """premium_change = base × k × (to/from − 1), capped at base × cap."""
    if base_premium <= 0 or from_limit <= 0:
        return Decimal(0)
    ratio_delta = (to_limit / from_limit) - Decimal(1)
    raw = base_premium * LIMIT_ELASTICITY_K * ratio_delta
    cap = base_premium * PREMIUM_CHANGE_CAP_MULT
    if raw > cap:
        raw = cap
    if raw < -cap:
        raw = -cap
    return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _find_limit_change(
    requested_changes: list[dict],
) -> tuple[str | None, Decimal, Decimal]:
    """Return (field_path, from_limit, to_limit) for the largest change.

    Pre-close-out we tracked only the delta, which made it impossible to
    compute a ratio. Now we return both endpoints so pricing can scale
    off `to/from` instead of raw delta.
    """
    best = (None, Decimal(0), Decimal(0), Decimal(0))  # path,from,to,|delta|
    for ch in requested_changes or []:
        fp = ch.get("field_path") or ""
        if "limits." not in fp and ".headcount" not in fp:
            continue
        fv = _dec(ch.get("from_value"))
        tv = _dec(ch.get("to_value"))
        delta = abs(tv - fv)
        if delta > best[3]:
            best = (fp, fv, tv, delta)
    return best[0], best[1], best[2]


async def submit_endorsement(
    *,
    envelope_id: str,
    submission: dict,
    policy_current: dict,
) -> dict[str, Any]:
    """Simulate a carrier response. Deterministic per envelope_id.

    Carrier identity is pinned to the policy's `insurer_display` so the
    quote → endorsement → certificate chain never crosses carriers.
    """
    ss = submission.get("structured_submission") or submission
    requested_changes = (
        ss.get("requested_changes")
        or submission.get("requested_changes") or []
    )
    field_path, from_v, to_v = _find_limit_change(requested_changes)

    base_premium = _dec(policy_current.get("premium_amount"))
    currency = policy_current.get("premium_currency") or "AUD"

    if field_path and ".headcount" in field_path:
        delta_amt = ((to_v - from_v) * PER_HEAD_ANNUAL).quantize(Decimal("0.01"))
        # Still cap at base to prevent runaway.
        cap = base_premium * PREMIUM_CHANGE_CAP_MULT
        if abs(delta_amt) > cap:
            delta_amt = cap if delta_amt > 0 else -cap
    elif field_path:
        delta_amt = _ratio_based_change(base_premium, from_v, to_v)
    else:
        # No limit change identified → nominal mid-term endorsement fee.
        delta_amt = (base_premium * Decimal("0.025")).quantize(Decimal("0.01"))

    new_annual = (base_premium + delta_amt).quantize(Decimal("0.01"))

    # Carrier identity — read from policy, never random.
    carrier = (
        policy_current.get("insurer_display")
        or policy_current.get("carrier_name")
        or "Fictional Carrier A"
    )

    conditions = [
        "Certificate must be issued within 30 days of bind.",
        "Continuous cover confirmed — no gap since last renewal.",
    ]
    terms = (
        "Quote valid for 30 days. Subject to executed endorsement wording."
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
