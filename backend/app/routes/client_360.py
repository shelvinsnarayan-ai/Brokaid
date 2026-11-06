"""Client 360 route (R5c-4b — Mongo-only)."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth.sessions import Session
from app.kernel import client_360 as client_360_kernel
from app.middleware.session import require_operator_session
from app.routes._scrub import scrub


router = APIRouter(prefix="/api/o")


def _store(request: Request):
    s = getattr(request.app.state, "mongo_db", None)
    if s is None:
        raise HTTPException(status_code=503, detail="mongo_store_unavailable")
    return s


def _parse(raw) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


@router.get("/clients")
async def list_clients(
    request: Request,
    session: Session = Depends(require_operator_session),
) -> dict:
    rows = await client_360_kernel.list_all_clients_with_policy_counts(
        _store(request), session.tenant_id,
    )
    return {"clients": rows}


@router.get("/client/{client_id}")
async def client_360(
    client_id: str, request: Request,
    session: Session = Depends(require_operator_session),
) -> dict:
    hdr = await client_360_kernel.get_client_header(
        _store(request), session.tenant_id, client_id,
    )
    if not hdr:
        raise HTTPException(status_code=404, detail="not_found")
    return {"client": hdr}


@router.get("/client/{client_id}/timeline")
async def client_timeline(
    client_id: str, request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(require_operator_session),
) -> dict:
    total, rows = await client_360_kernel.get_client_timeline(
        _store(request), session.tenant_id, client_id,
        limit=limit, offset=offset,
    )
    events = []
    for r in rows:
        ts = r["ts"]
        events.append({
            "event_id": r["event_id"],
            "seq": int(r["seq"]),
            "ts": (
                ts.isoformat(timespec="microseconds")
                if hasattr(ts, "isoformat") else ts
            ),
            "type": r["type"],
            "actor": scrub(_parse(r["actor"])),
            "payload": scrub(_parse(r["payload"])),
        })
    from app.kernel.event_text import render
    await render(_store(request), session.tenant_id, events)
    return {
        "client_id": client_id, "total": total,
        "limit": limit, "offset": offset, "events": events,
    }
