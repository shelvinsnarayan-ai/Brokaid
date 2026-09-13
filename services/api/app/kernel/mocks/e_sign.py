"""MOCKED e-sign adapter.

Deterministic per envelope_id. FICTIONAL — no real signing engine.
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any


def _det_uuid(seed: str) -> str:
    return str(uuid.UUID(bytes=hashlib.sha256(seed.encode()).digest()[:16]))


async def request_signature(
    *, envelope_id: str, document_ref: str, signer_email: str,
) -> dict[str, Any]:
    await asyncio.sleep(0.05)
    return {
        "request_id": _det_uuid(f"esign:req:{envelope_id}"),
        "document_ref": document_ref,
        "signer_email": signer_email,
        "status": "sent",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "mocked": True,
    }


async def simulate_signature(
    *, envelope_id: str, request_id: str,
) -> dict[str, Any]:
    """Auto-sign path used by the phase-11 demo scene."""
    await asyncio.sleep(0.15)
    fake_hash = hashlib.sha256(
        f"signature:{envelope_id}:{request_id}".encode()
    ).hexdigest()
    return {
        "signature_id": _det_uuid(f"esign:sig:{envelope_id}"),
        "request_id": request_id,
        "signature_blob_hash": fake_hash,
        "signed_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "status": "signed",
        "mocked": True,
    }
