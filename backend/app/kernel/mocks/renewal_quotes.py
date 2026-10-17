"""MOCKED insurer renewal quotes (R1.6). Bounded: anchored to the expiring premium ±15%, excess the same or one step up.

Deterministic per (renewal, insurer) so a re-request gives the same price. Every quote carries data_basis MOCKED.
"""
from __future__ import annotations

import hashlib
from decimal import ROUND_HALF_UP, Decimal

MAX_INSURERS = 3
BAND = Decimal("0.15")
TERMS = ("Same cover as expiring", "Claims-free discount applied", "Adds tools of trade cover to $5,000",
         "Hire car benefit removed", "Extra $500 excess for drivers under 25", "Flood cover included as standard",
         "Monthly instalments available (MOCKED)", "Business interruption extended to 12 months")


def quote(insurer: str, expiring_premium, expiring_excess, seed: str) -> dict:
    h = hashlib.sha256(f"{seed}:{insurer}".encode()).digest()
    u = Decimal(int.from_bytes(h[:4], "big")) / Decimal(2 ** 32) * 2 * BAND - BAND  # in [-15%, +15%)
    base = Decimal(str(expiring_premium or 0))
    premium = (base * (1 + u)).quantize(Decimal("0.01"), ROUND_HALF_UP)
    exc = Decimal(str(expiring_excess or 500))
    step = (Decimal("1"), Decimal("1"), Decimal("1.5"))[h[4] % 3]
    excess = ((exc * step) / 50).quantize(Decimal("1"), ROUND_HALF_UP) * 50
    terms = [TERMS[0] if step == 1 else f"Excess raised to ${excess:,.0f}"] + [TERMS[1 + h[i] % (len(TERMS) - 1)] for i in (5, 6)]
    return {"insurer": insurer, "premium": str(premium), "excess": str(excess), "key_terms": list(dict.fromkeys(terms)),
            "data_basis": "MOCKED", "mocked": True}
