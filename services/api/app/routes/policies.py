"""Program View — operator and client scoped policy listings."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.sessions import Session
from app.kernel import jurisdiction
from app.middleware.session import require_operator_session, require_session

router = APIRouter()


def _row_to_policy(r: dict, pack) -> dict:
    return {
        "policy_id": str(r["policy_id"]),
        "client_id": str(r["client_id"]),
        "status": r["status"],
        "product": r["product"],
        "insurer_display": r["insurer_display"],
        "inception": r["inception"].isoformat(timespec="microseconds"),
        "expiry": r["expiry"].isoformat(timespec="microseconds"),
        "premium": {
            "amount": float(r["premium_amount"]),
            "currency": pack.currency_code,
            "symbol": pack.currency_symbol,
        },
        "schedule": r["schedule"] if isinstance(r["schedule"], dict) else {},
        "imported_from": r["imported_from"],
        "mocked_insurer": True,
    }


async def _fetch_client_row(conn, tenant_id: str, client_id: str):
    return await conn.fetchrow(
        "SELECT client_id::text, display_name, jurisdiction_code "
        "FROM clients WHERE tenant_id = $1::uuid AND client_id = $2::uuid",
        tenant_id, client_id,
    )


async def _resolve_pack(conn, tenant_id: str, client_id: str, override: str | None):
    if override and override in ("AU", "SG"):
        return jurisdiction.get(override), True
    row = await _fetch_client_row(conn, tenant_id, client_id)
    code = row["jurisdiction_code"] if row else "AU"
    return jurisdiction.get(code), False


@router.get("/api/o/policies")
async def list_operator_policies(
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
                "SELECT policy_id, client_id, status, product, insurer_display, "
                "       inception, expiry, premium_amount, premium_currency, "
                "       schedule, imported_from "
                "  FROM policies WHERE tenant_id = $1::uuid "
                "  ORDER BY inception DESC",
                session.tenant_id,
            )
            packs_by_client: dict[str, tuple] = {}
            out = []
            for r in rows:
                cid = str(r["client_id"])
                if cid not in packs_by_client:
                    packs_by_client[cid] = await _resolve_pack(
                        c, session.tenant_id, cid, override=None
                    )
                pack, _ = packs_by_client[cid]
                out.append(_row_to_policy(dict(r), pack))
    return {"policies": out}


@router.get("/api/c/policies")
async def list_client_policies(
    request: Request, session: Session = Depends(require_session),
) -> dict:
    if session.client_id is None:
        raise HTTPException(status_code=403, detail="no_client_context")
    pool = request.app.state.pool
    override = None
    if isinstance(session.__dict__.get("client_prefs"), dict):
        override = session.__dict__["client_prefs"].get("jurisdiction")
    # session dataclass doesn't include prefs; fetch fresh row for override.
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT client_prefs FROM sessions WHERE session_id = $1::uuid",
            session.session_id,
        )
    if row and isinstance(row["client_prefs"], dict):
        override = row["client_prefs"].get("jurisdiction") or override
    elif row and isinstance(row["client_prefs"], str):
        import json as _j
        try:
            override = _j.loads(row["client_prefs"]).get("jurisdiction") or override
        except Exception:
            pass

    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", session.tenant_id
            )
            client_row = await _fetch_client_row(
                c, session.tenant_id, session.client_id
            )
            pack, is_preview = await _resolve_pack(
                c, session.tenant_id, session.client_id, override
            )
            rows = await c.fetch(
                "SELECT policy_id, client_id, status, product, insurer_display, "
                "       inception, expiry, premium_amount, premium_currency, "
                "       schedule, imported_from "
                "  FROM policies "
                " WHERE tenant_id = $1::uuid AND client_id = $2::uuid "
                " ORDER BY inception DESC",
                session.tenant_id, session.client_id,
            )
    actual_code = client_row["jurisdiction_code"] if client_row else "AU"
    return {
        "policies": [_row_to_policy(dict(r), pack) for r in rows],
        "jurisdiction": pack.to_dict(),
        "actual_jurisdiction": actual_code,
        "preview_mode": is_preview and pack.code != actual_code,
    }
