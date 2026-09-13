"""MOCKED insurer lodgement channel.

Every response carries `mocked=True` and ledger events wrapping these
calls MUST carry `mocked: true`. The client-facing timeline and the
operator claim card MUST render a `MockedBadge` with label=`insurer`.
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any


FICTIONAL_INSURERS = [
    "Fictional Marine & General",
    "Southern Cross Underwriters (Fictional)",
    "Pacific Rim Assurance (Fictional)",
    "Meridian Specialty (Fictional)",
]


def _det_uuid(seed: str) -> str:
    return str(uuid.UUID(bytes=hashlib.sha256(seed.encode()).digest()[:16]))


def _pick_insurer(claim_id: str) -> str:
    idx = int(hashlib.sha256(claim_id.encode()).hexdigest(), 16) % len(FICTIONAL_INSURERS)
    return FICTIONAL_INSURERS[idx]


async def lodge_claim(claim: dict[str, Any]) -> dict[str, Any]:
    """Simulate submitting a claim to an insurer.

    Deterministic per `claim_id`. Latency ~300 ms.
    Returns { lodgement_id, insurer_display, insurer_reference,
              received_at, mocked }.
    """
    claim_id = str(claim.get("claim_id") or "")
    if not claim_id:
        raise ValueError("claim.claim_id required")
    await asyncio.sleep(0.3)
    insurer = _pick_insurer(claim_id)
    return {
        "lodgement_id": _det_uuid(f"lodgement:{claim_id}"),
        "insurer_display": insurer,
        "insurer_reference": f"MOCK-{insurer.split()[0].upper()}-{claim_id[:8]}",
        "received_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "mocked": True,
    }
