"""Program View — operator and client scoped policy listings (R5c-4b — Mongo-only)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.sessions import Session
from app.db.tenant_safe import wrap as _wrap_safe
from app.kernel import jurisdiction
from app.kernel.tenant_ctx import use_tenant
from app.middleware.session import require_operator_session, require_session
from app.kernel.test_data import real_policy_filter, test_client_ids, test_event_filter  # noqa: F401

router = APIRouter()


def _store(request: Request):
    s = getattr(request.app.state, "mongo_db", None)
    if s is None:
        raise HTTPException(status_code=503, detail="mongo_store_unavailable")
    return s


def _iso(dt) -> str:
    return dt.isoformat(timespec="microseconds") if hasattr(dt, "isoformat") else str(dt)


def _row_to_policy(r: dict, pack) -> dict:
    return {
        "policy_id": str(r["policy_id"]),
        "client_id": str(r["client_id"]),
        "status": r["status"],
        "product": r["product"],
        "insurer_display": r["insurer_display"],
        "inception": _iso(r["inception"]),
        "expiry": _iso(r["expiry"]),
        "premium": {
            "amount": float(r["premium_amount"]),
            "currency": pack.currency_code,
            "symbol": pack.currency_symbol,
        },
        "schedule": r["schedule"] if isinstance(r["schedule"], dict) else {},
        "imported_from": r["imported_from"],
        "mocked_insurer": True,
    }


async def _fetch_client_row(store, tenant_id: str, client_id: str):
    safe = _wrap_safe(store)
    async with use_tenant(tenant_id):
        row = await safe.clients.find_one({"client_id": client_id})
    return row


async def _resolve_pack(store, tenant_id: str, client_id: str, override: str | None):
    if override and override in ("AU", "SG"):
        return jurisdiction.get(override), True
    row = await _fetch_client_row(store, tenant_id, client_id)
    code = (row or {}).get("jurisdiction_code") or "AU"
    return jurisdiction.get(code), False


async def _list_policies(
    store, tenant_id: str, client_id: str | None = None,
) -> list[dict]:
    safe = _wrap_safe(store)
    out: list[dict] = []
    async with use_tenant(tenant_id):
        filt = {"client_id": client_id} if client_id else await real_policy_filter(safe)
        cursor = safe.policies.find(filt).sort("inception", -1)
        packs_by_client: dict[str, tuple] = {}
        async for r in cursor:
            cid = str(r["client_id"])
            if client_id is None and cid not in packs_by_client:
                packs_by_client[cid] = await _resolve_pack(
                    store, tenant_id, cid, override=None,
                )
            pack = (
                packs_by_client[cid][0] if client_id is None
                else None
            )
            row_norm = {
                "policy_id": r["policy_id"],
                "client_id": r["client_id"],
                "status": r.get("status"),
                "product": r.get("product"),
                "insurer_display": r.get("insurer_display"),
                "inception": r.get("inception"),
                "expiry": r.get("expiry"),
                "premium_amount": r.get("premium_amount") or 0,
                "premium_currency": r.get("premium_currency"),
                "schedule": r.get("schedule") or {},
                "imported_from": r.get("imported_from"),
            }
            if pack is None:
                out.append(row_norm)
            else:
                out.append(_row_to_policy(row_norm, pack))
    return out


async def _read_session_override(store, session_id: str) -> str | None:
    safe = _wrap_safe(store)
    row = await safe.system("sessions").find_one({"session_id": session_id})
    prefs = (row or {}).get("client_prefs") or {}
    if isinstance(prefs, dict):
        return prefs.get("jurisdiction")
    return None


@router.get("/api/o/policies")
async def list_operator_policies(
    request: Request,
    session: Session = Depends(require_operator_session),
) -> dict:
    store = _store(request)
    out = await _list_policies(store, session.tenant_id)
    return {"policies": out}


@router.get("/api/c/policies")
async def list_client_policies(
    request: Request, session: Session = Depends(require_session),
) -> dict:
    if session.client_id is None:
        raise HTTPException(status_code=403, detail="no_client_context")
    store = _store(request)
    override = await _read_session_override(store, session.session_id)
    pack, is_preview = await _resolve_pack(
        store, session.tenant_id, session.client_id, override,
    )
    client_row = await _fetch_client_row(
        store, session.tenant_id, session.client_id,
    )
    rows_raw = await _list_policies(
        store, session.tenant_id, client_id=session.client_id,
    )
    policies = [_row_to_policy(r, pack) for r in rows_raw]
    actual_code = (client_row or {}).get("jurisdiction_code") or "AU"
    return {
        "policies": policies,
        "jurisdiction": pack.to_dict(),
        "actual_jurisdiction": actual_code,
        "preview_mode": is_preview and pack.code != actual_code,
    }
