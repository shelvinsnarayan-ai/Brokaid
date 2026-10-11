"""MOCKED payment details (BPAY + EFT).

Payments are advised, never processed (Build Brief v3.0 §11, A12a M3-5).
This module only builds static, MOCKED remittance details for display;
there is no gateway, mandate or receipt.
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

BPAY_BILLER_CODE_MOCKED = "999999"
EFT_BSB_MOCKED = "000-000"
EFT_ACCOUNT_MOCKED = "00000000"
EFT_ACCOUNT_NAME_MOCKED = "Broker trust account"
DUE_IN_DAYS = 7


def _bpay_reference(envelope_id: str) -> str:
    digest = hashlib.sha256(envelope_id.encode("utf-8")).hexdigest()
    return str(int(digest[:12], 16))[:10].rjust(10, "0")


def payment_details(
    *, envelope_id: str, amount: Decimal | str | float, currency: str,
    today: date | None = None,
) -> dict[str, Any]:
    due = (today or date.today()) + timedelta(days=DUE_IN_DAYS)
    return {
        "amount": str(Decimal(str(amount)).quantize(Decimal("0.01"))),
        "currency": currency,
        "due_date": due.isoformat(),
        "bpay": {
            "biller_code": BPAY_BILLER_CODE_MOCKED,
            "reference": _bpay_reference(envelope_id),
            "mocked": True,
        },
        "eft": {
            "account_name": EFT_ACCOUNT_NAME_MOCKED,
            "bsb": EFT_BSB_MOCKED,
            "account_number": EFT_ACCOUNT_MOCKED,
            "reference": _bpay_reference(envelope_id),
            "mocked": True,
        },
        "mocked": True,
    }
