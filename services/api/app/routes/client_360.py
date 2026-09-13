"""Minimal Client 360 — header aggregate + paginated ledger timeline."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth.sessions import Session
from app.middleware.session import require_operator_session
from app.routes._scrub import scrub


router = APIRouter(prefix="/api/o")


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
    pool = request.app.state.pool
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", session.tenant_id
            )
            rows = await c.fetch(
                """
                SELECT c.client_id::text AS client_id, c.display_name,
                       c.industry, c.domicile, c.jurisdiction_code,
                       COUNT(p.policy_id) AS policy_count
                  FROM clients c
                  LEFT JOIN policies p
                    ON p.tenant_id = c.tenant_id AND p.client_id = c.client_id
                 WHERE c.tenant_id = $1::uuid
                 GROUP BY c.client_id
                 ORDER BY c.display_name ASC
                """,
                session.tenant_id,
            )
    return {"clients": [dict(r) for r in rows]}


@router.get("/client/{client_id}")
async def client_360(
    client_id: str, request: Request,
    session: Session = Depends(require_operator_session),
) -> dict:
    pool = request.app.state.pool
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", session.tenant_id
            )
            client_row = await c.fetchrow(
                "SELECT client_id::text AS client_id, display_name, industry, "
                "       domicile, jurisdiction_code "
                "  FROM clients WHERE tenant_id = $1::uuid AND client_id = $2::uuid",
                session.tenant_id, client_id,
            )
            if not client_row:
                raise HTTPException(status_code=404, detail="not_found")
            active_policies = await c.fetchval(
                "SELECT COUNT(*) FROM policies "
                " WHERE tenant_id = $1::uuid AND client_id = $2::uuid "
                "   AND status IN ('managed','tracked')",
                session.tenant_id, client_id,
            )
            open_matters = await c.fetchval(
                "SELECT COUNT(*) FROM proj_matters "
                " WHERE tenant_id = $1::uuid AND client_id = $2::uuid "
                "   AND status <> 'closed'",
                session.tenant_id, client_id,
            )
    return {
        "client": {
            **dict(client_row),
            "active_policies": int(active_policies or 0),
            "open_matters": int(open_matters or 0),
        },
    }


@router.get("/client/{client_id}/timeline")
async def client_timeline(
    client_id: str, request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(require_operator_session),
) -> dict:
    pool = request.app.state.pool
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", session.tenant_id
            )
            total = await c.fetchval(
                "SELECT COUNT(*) FROM events "
                " WHERE tenant_id = $1::uuid "
                "   AND (payload->>'client_id' = $2 OR actor->>'id' = $2)",
                session.tenant_id, client_id,
            )
            rows = await c.fetch(
                """
                SELECT event_id::text AS event_id, seq, ts, type, actor, payload
                  FROM events
                 WHERE tenant_id = $1::uuid
                   AND (payload->>'client_id' = $2 OR actor->>'id' = $2)
                 ORDER BY seq DESC
                 LIMIT $3 OFFSET $4
                """,
                session.tenant_id, client_id, limit, offset,
            )
    return {
        "client_id": client_id,
        "total": int(total or 0),
        "limit": limit, "offset": offset,
        "events": [
            {
                "event_id": r["event_id"],
                "seq": int(r["seq"]),
                "ts": r["ts"].isoformat(timespec="microseconds"),
                "type": r["type"],
                "actor": scrub(_parse(r["actor"])),
                "payload": scrub(_parse(r["payload"])),
            }
            for r in rows
        ],
    }
