"""MOCKED payment rail.

Deterministic per envelope_id. FICTIONAL rails (PayTo / PayNow).
Every response carries `mocked=True`; ledger events wrapping these
calls MUST carry `mocked: true`. The client-facing pay screen MUST
render a MockedBadge with `label="payment"`.
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal


RailLabel = Literal["PayTo", "PayNow"]


def _det_uuid(seed: str) -> str:
    return str(uuid.UUID(bytes=hashlib.sha256(seed.encode()).digest()[:16]))


async def initiate_mandate(
    *,
    envelope_id: str,
    client_id: str,
    amount: Decimal | str | float,
    currency: str,
    rail_label: RailLabel,
) -> dict[str, Any]:
    """Create a mocked payment mandate. Deterministic per envelope_id."""
    await asyncio.sleep(0.05)      # simulate a hair of latency
    return {
        "mandate_id": _det_uuid(f"mandate:{envelope_id}"),
        "client_id": client_id,
        "amount": str(Decimal(str(amount)).quantize(Decimal("0.01"))),
        "currency": currency,
        "rail_label": rail_label,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "mocked": True,
    }


async def execute_payment(
    *,
    envelope_id: str,
    mandate_id: str,
    quote_id: str,
    amount: Decimal | str | float,
    currency: str,
    rail_label: RailLabel,
) -> dict[str, Any]:
    """Execute a mocked payment against a mandate. Always succeeds in phase 11."""
    await asyncio.sleep(0.2)       # ~200ms simulated latency
    return {
        "payment_id": _det_uuid(f"payment:{envelope_id}:{mandate_id}"),
        "mandate_id": mandate_id,
        "quote_id": quote_id,
        "amount": str(Decimal(str(amount)).quantize(Decimal("0.01"))),
        "currency": currency,
        "rail_label": rail_label,
        "status": "succeeded",
        "ref": f"MOCK-{rail_label.upper()}-{_det_uuid(f'ref:{envelope_id}')[:8]}",
        "settled_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "mocked": True,
    }
