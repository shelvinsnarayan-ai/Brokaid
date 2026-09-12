"""GET /api/trail/export — chain-proof export for a tenant."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.kernel.trail import event_to_json, read_events

router = APIRouter(prefix="/api/trail")

VERIFIER_JS = (
    Path(__file__).resolve().parents[4] / "packages" / "kernel" / "verifier" / "verify.js"
)


def _verifier_sha256() -> str:
    if not VERIFIER_JS.exists():
        return "0" * 64
    return hashlib.sha256(VERIFIER_JS.read_bytes()).hexdigest()


@router.get("/export")
async def export_trail(
    request: Request,
    tenant: str,
    from_seq: int | None = None,
    to_seq: int | None = None,
) -> dict:
    pool = request.app.state.pool
    try:
        t, events = await read_events(pool, tenant, from_seq=from_seq, to_seq=to_seq)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "schema_version": "trail/v1",
        "tenant": {
            "tenant_id": t.tenant_id,
            "slug": t.slug,
            "display_name": t.display_name,
            "genesis_hash": t.genesis_hash,
        },
        "verifier_binary_sha256": _verifier_sha256(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "events": [event_to_json(e) for e in events],
    }
