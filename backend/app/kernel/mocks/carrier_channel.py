"""MOCKED CarrierChannel.

FICTIONAL PRICING — NOT REAL MARKET RATES.
Deterministic per envelope_id so the same submission always produces
the same quote (RNG seeded on envelope_id).

R1 close-out (user prod verification 2026-10-02):
  · Pricing is a ratio-of-base-premium so a $10M→$30M PL uplift on
    an $8,500 policy quotes a plausible ~$2,550 — not the $1.35M
    nonsense that came out of the old `delta_pct_of_limit` rule.
  · Carrier identity is READ FROM THE POLICY (`insurer_display`), not
    randomised. One policy = one carrier end-to-end.

R1 Blocker-1 regression fix (2026-10-04):
  · `current_limit` is now read from the authoritative
    `schedule.cover_types[slug].limit_any_one` on the policy — NOT
    from `schedule.limits.<slug>` and NOT from the LLM-reported
    `from_value` (which was coming back as 0 on policies whose seed
    had no mirrored `limits` map, driving the premium delta to $0).
  · If no authoritative current_limit is present on the policy for
    the requested slug, we raise `MissingCurrentLimitError` — the
    envelope parks (silence parks) rather than ship a $0 quote.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


class MissingCurrentLimitError(RuntimeError):
    """Raised when the policy doesn't carry an authoritative
    `schedule.cover_types[*].limit_any_one` for the slug being
    endorsed. Blocks the mocked quote so no $0 nonsense reaches
    the client."""


# k = elasticity of premium to limit change. Tuned so that for a
# doubling of the limit (ratio=2) the premium rises by ~15% of base.
# Capped at 1.0× base so no mocked quote can ever balloon into the
# user's reported pathology.
LIMIT_ELASTICITY_K = Decimal("0.15")
PREMIUM_CHANGE_CAP_MULT = Decimal("1.0")

# Per-headcount uplift kept as before (not a limit path).
PER_HEAD_ANNUAL = Decimal("120.00")

# Vehicle operations (R0.2): base × k × (net delta / current count),
# |change| floored at $150 and capped at 0.5 × base; 1-for-1 swap = flat fee.
VEHICLE_K = Decimal("0.08")
VEHICLE_FLOOR = Decimal("150.00")
VEHICLE_CAP_MULT = Decimal("0.5")
VEHICLE_SWAP_ADMIN_FEE = Decimal("85.00")


def vehicle_change_premium(
    base_premium: Decimal, current_count: int, vehicle_ops: list[dict],
) -> Decimal:
    net = 0
    for c in vehicle_ops:
        op = (c.get("operation") or c.get("op") or "").lower()
        net += 1 if op == "add" else -1 if op == "remove" else 0
    if net == 0:
        return VEHICLE_SWAP_ADMIN_FEE
    raw = abs(base_premium * VEHICLE_K * Decimal(net) / Decimal(max(current_count, 1)))
    amt = min(max(raw, VEHICLE_FLOOR), base_premium * VEHICLE_CAP_MULT)
    amt = amt.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return amt if net > 0 else -amt


def _dec(v) -> Decimal:
    if v is None:
        return Decimal(0)
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal(0)


def _slug(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "_")


def _extract_limit_slug(field_path: str) -> str | None:
    """Pull the limit slug from a `schedule.limits.<slug>` field_path.

    Nested limit paths (e.g. `schedule.limits.cyber.aggregate`) collapse
    to their first segment (`cyber`) so cover_types lookup still works.
    """
    if "limits." not in field_path:
        return None
    tail = field_path.split("limits.", 1)[1]
    return tail.split(".")[0] if tail else None


def _lookup_current_limit(policy_current: dict, slug: str) -> Decimal | None:
    """Read `schedule.cover_types[match slug].limit_any_one` — the
    authoritative display field.
    """
    schedule = policy_current.get("schedule") or {}
    cover_types = schedule.get("cover_types") or []
    if not isinstance(cover_types, list):
        return None
    for ct in cover_types:
        if not isinstance(ct, dict):
            continue
        if _slug(ct.get("type")) == slug:
            v = ct.get("limit_any_one")
            if v is None:
                return None
            d = _dec(v)
            return d if d > 0 else None
    return None


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
    """Return (field_path, from_value_hint, to_value) for the biggest
    change by absolute delta.

    `from_value_hint` is kept only for backward compatibility with the
    headcount path; the limit path now ignores it in favour of the
    policy's authoritative `cover_types[*].limit_any_one`.
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
    field_path, from_hint, to_v = _find_limit_change(requested_changes)

    base_premium = _dec(policy_current.get("premium_amount"))
    currency = policy_current.get("premium_currency") or "AUD"
    vehicle_ops = [c for c in requested_changes
                   if (c.get("field_path") or "").startswith("schedule.vehicles")]

    if vehicle_ops:
        current_count = len((policy_current.get("schedule") or {}).get("vehicles") or [])
        delta_amt = vehicle_change_premium(base_premium, current_count, vehicle_ops)
    elif field_path and ".headcount" in field_path:
        delta_amt = ((to_v - from_hint) * PER_HEAD_ANNUAL).quantize(Decimal("0.01"))
        cap = base_premium * PREMIUM_CHANGE_CAP_MULT
        if abs(delta_amt) > cap:
            delta_amt = cap if delta_amt > 0 else -cap
    elif field_path:
        slug = _extract_limit_slug(field_path)
        if not slug:
            raise MissingCurrentLimitError(
                f"could not parse limit slug from field_path {field_path!r}"
            )
        current_limit = _lookup_current_limit(policy_current, slug)
        if current_limit is None:
            raise MissingCurrentLimitError(
                f"policy {policy_current.get('policy_id')!r} is missing an "
                f"authoritative schedule.cover_types[*].limit_any_one for "
                f"slug {slug!r} — cannot price endorsement."
            )
        delta_amt = _ratio_based_change(base_premium, current_limit, to_v)
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
