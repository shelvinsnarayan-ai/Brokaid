"""GET /api/trail/export — chain-proof export for a tenant (R5c-4b — Mongo-only)."""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from app.auth.sessions import Session
from app.db.tenant_safe import wrap as _wrap_safe
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail import event_to_json
from app.kernel.trail_dispatch import read_events as read_events_dispatch
from app.middleware.session import require_operator_session

router = APIRouter(prefix="/api/trail")

VERIFIER_JS = (
    Path(__file__).resolve().parents[3] / "packages" / "kernel" / "verifier" / "verify.js"
)


def _store(request: Request):
    s = getattr(request.app.state, "mongo_db", None)
    if s is None:
        raise HTTPException(status_code=503, detail="mongo_store_unavailable")
    return s


def _verifier_sha256() -> str:
    if not VERIFIER_JS.exists():
        return "0" * 64
    return hashlib.sha256(VERIFIER_JS.read_bytes()).hexdigest()


def _iso_or_none(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"bad date: {e}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_event_ts(ts) -> datetime:
    if isinstance(ts, datetime):
        dt = ts
    else:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _filter_events(events, client_id, dt_from, dt_to):
    out = []
    for e in events:
        if dt_from or dt_to:
            e_ts = _parse_event_ts(e.ts)
            if dt_from and e_ts < dt_from:
                continue
            if dt_to and e_ts > dt_to:
                continue
        if client_id:
            payload = e.payload or {}
            if str(payload.get("client_id") or "") != client_id:
                continue
        out.append(e)
    return out


async def _load_scoped(store, tenant_id, client_id, dt_from, dt_to):
    t, events = await read_events_dispatch(store, tenant_id)
    filtered = _filter_events(events, client_id, dt_from, dt_to)
    return t, filtered


def _build_export_doc(t, events) -> dict:
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


@router.get("/export")
async def export_trail(
    request: Request,
    tenant: str,
    from_seq: int | None = None,
    to_seq: int | None = None,
    client_id: str | None = Query(None),
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
) -> dict:
    store = _store(request)
    try:
        t, events = await read_events_dispatch(
            store, tenant, from_seq=from_seq, to_seq=to_seq,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    events = _filter_events(events, client_id, _iso_or_none(date_from), _iso_or_none(date_to))
    return _build_export_doc(t, events)


@router.get("/export/preview")
async def export_preview(
    request: Request,
    client_id: str | None = Query(None),
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    session: Session = Depends(require_operator_session),
) -> dict:
    _, events = await _load_scoped(
        _store(request), session.tenant_id, client_id,
        _iso_or_none(date_from), _iso_or_none(date_to),
    )
    types = Counter(e.type for e in events)
    return {
        "event_count": len(events),
        "event_types_summary": [
            {"type": k, "count": v}
            for k, v in sorted(types.items(), key=lambda x: -x[1])
        ],
    }


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(s: str) -> str:
    return _SLUG_RE.sub("-", s.lower()).strip("-") or "client"


async def _lookup_client_display(store, tenant_id: str, client_id: str) -> str | None:
    """Client display name lookup for the bundle filename."""
    safe = _wrap_safe(store)
    async with use_tenant(tenant_id):
        row = await safe.clients.find_one({"client_id": client_id})
    return (row or {}).get("display_name") if row else None


@router.get("/export/bundle")
async def export_bundle(
    request: Request,
    client_id: str | None = Query(None),
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    session: Session = Depends(require_operator_session),
) -> Response:
    store = _store(request)
    t, events = await _load_scoped(
        store, session.tenant_id, client_id,
        _iso_or_none(date_from), _iso_or_none(date_to),
    )
    export_doc = _build_export_doc(t, events)

    verifier_bytes = (
        VERIFIER_JS.read_bytes()
        if VERIFIER_JS.exists() else b"// verifier missing\n"
    )
    verifier_sha = _verifier_sha256()

    client_display = None
    if client_id:
        client_display = await _lookup_client_display(
            store, session.tenant_id, client_id,
        )
    client_slug = _slug(client_display or client_id or "all")
    from_tag = (date_from or "start")[:10]
    to_tag = (date_to or "now")[:10]
    filename = f"brokaid-ledger-export-{client_slug}-{from_tag}-{to_tag}.zip"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("export.json", json.dumps(export_doc, indent=2))
        z.writestr("verify.js", verifier_bytes)
        z.writestr("verifier_binary_sha256.txt", verifier_sha + "\n")
        z.writestr("README.md", _README_TEXT.format(
            filename=filename,
            client_display=client_display or "all clients in the tenant",
            event_count=len(events),
            verifier_sha=verifier_sha,
        ))
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


_README_TEXT = """# brokAId ledger export bundle

**Filename**: `{filename}`
**Scope**: {client_display}
**Events in bundle**: {event_count}
**Bundled verifier SHA-256**: `{verifier_sha}`

This zip lets you verify the ledger's integrity **without** contacting
our servers or trusting any code we control at verification time.

## How to run

```
unzip {filename}
cd $(basename {filename} .zip)
node --version   # v18+
npm i canonicalize
node verify.js export.json
```
"""
