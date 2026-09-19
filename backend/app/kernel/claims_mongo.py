"""Phase R4c — Mongo backend for the claims layer.

Two collections:
  claims                     - authoritative claim row (tenant-scoped)
  proj_claim_status_updates  - status timeline projection (tenant-scoped)

Both accessed through `TenantSafeDatabase`. Public API mirrors what
the existing routes (`c_claims`, `o_claims`) call inline SQL for.
Claim reference sequence (`CLM-YYYY-NNNNNN`) is computed under an
advisory lock via `_counters` — same pattern the Postgres path uses.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from app.db.tenant_safe import TenantSafeDatabase, wrap
from app.kernel.tenant_ctx import get_tenant_id


_indexes_initialised: set[int] = set()


async def _ensure_indexes(safe_db: TenantSafeDatabase) -> None:
    raw = safe_db.raw
    key = id(raw)
    if key in _indexes_initialised:
        return
    await raw["claims"].create_index(
        [("tenant_id", ASCENDING), ("claim_id", ASCENDING)],
        unique=True, name="claims_tenant_id_uniq",
    )
    await raw["claims"].create_index(
        [("tenant_id", ASCENDING), ("claim_reference", ASCENDING)],
        unique=True, name="claims_tenant_ref_uniq",
    )
    await raw["claims"].create_index(
        [("tenant_id", ASCENDING), ("client_id", ASCENDING)],
        name="claims_tenant_client",
    )
    await raw["proj_claim_status_updates"].create_index(
        [("tenant_id", ASCENDING), ("claim_id", ASCENDING), ("seq", ASCENDING)],
        unique=True, name="pcsu_tenant_claim_seq",
    )
    _indexes_initialised.add(key)


@dataclass(frozen=True)
class Claim:
    claim_id: str
    tenant_id: str
    client_id: str
    policy_id: Optional[str]
    claim_reference: str
    incident_date: Optional[str]
    description: str
    claim_type: Optional[str]
    urgency: Optional[str]
    status: str
    opened_at: datetime
    resolved_at: Optional[datetime]
    opened_by_event: str


async def create_claim(
    db, *, claim_id: str, client_id: str,
    claim_reference: str, description: str, status: str,
    opened_by_event: str,
    policy_id: Optional[str] = None,
    incident_date: Optional[str] = None,
    claim_type: Optional[str] = None,
    urgency: Optional[str] = None,
) -> Claim:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    tenant_id = get_tenant_id()
    now = datetime.now(timezone.utc)
    try:
        await safe_db.claims.insert_one({
            "claim_id": claim_id, "tenant_id": tenant_id,
            "client_id": client_id, "policy_id": policy_id,
            "claim_reference": claim_reference,
            "incident_date": incident_date,
            "description": description, "claim_type": claim_type,
            "urgency": urgency, "status": status,
            "opened_at": now, "resolved_at": None,
            "opened_by_event": opened_by_event,
        })
    except DuplicateKeyError:
        pass
    return Claim(
        claim_id=claim_id, tenant_id=tenant_id, client_id=client_id,
        policy_id=policy_id, claim_reference=claim_reference,
        incident_date=incident_date, description=description,
        claim_type=claim_type, urgency=urgency, status=status,
        opened_at=now, resolved_at=None,
        opened_by_event=opened_by_event,
    )


async def find_claim(db, claim_id: str) -> Optional[Claim]:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    r = await safe_db.claims.find_one({"claim_id": claim_id})
    if r is None:
        return None
    return Claim(
        claim_id=r["claim_id"], tenant_id=r["tenant_id"],
        client_id=r["client_id"], policy_id=r.get("policy_id"),
        claim_reference=r["claim_reference"],
        incident_date=r.get("incident_date"),
        description=r["description"], claim_type=r.get("claim_type"),
        urgency=r.get("urgency"), status=r["status"],
        opened_at=r["opened_at"], resolved_at=r.get("resolved_at"),
        opened_by_event=r["opened_by_event"],
    )


async def update_claim_status(
    db, claim_id: str, status: str, resolved: bool = False,
) -> None:
    safe_db = wrap(db)
    update: dict[str, Any] = {"status": status}
    if resolved:
        update["resolved_at"] = datetime.now(timezone.utc)
    await safe_db.claims.update_one(
        {"claim_id": claim_id},
        {"$set": update},
    )


async def list_claims_for_client(db, client_id: str) -> list[Claim]:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    get_tenant_id()
    out = []
    async for r in safe_db.claims.find({"client_id": client_id}).sort(
        "opened_at", ASCENDING,
    ):
        out.append(Claim(
            claim_id=r["claim_id"], tenant_id=r["tenant_id"],
            client_id=r["client_id"], policy_id=r.get("policy_id"),
            claim_reference=r["claim_reference"],
            incident_date=r.get("incident_date"),
            description=r["description"], claim_type=r.get("claim_type"),
            urgency=r.get("urgency"), status=r["status"],
            opened_at=r["opened_at"], resolved_at=r.get("resolved_at"),
            opened_by_event=r["opened_by_event"],
        ))
    return out


# ---- proj_claim_status_updates ---------------------------------------
async def append_status_update(
    db, *, claim_id: str, event_id: str, ts: datetime,
    kind: str, summary: str, mocked: bool = False,
) -> int:
    """Append a status update. Sequence is monotonic per (tenant, claim)."""
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    # Next seq — count existing + 1. Under the tenant filter this is
    # safe (no cross-tenant leak).
    cnt = await safe_db.proj_claim_status_updates.count_documents(
        {"claim_id": claim_id}
    )
    seq = cnt + 1
    await safe_db.proj_claim_status_updates.insert_one({
        "claim_id": claim_id, "seq": seq,
        "event_id": event_id, "ts": ts,
        "kind": kind, "summary": summary, "mocked": bool(mocked),
    })
    return seq


async def list_status_updates(db, claim_id: str) -> list[dict[str, Any]]:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    out = []
    async for r in safe_db.proj_claim_status_updates.find(
        {"claim_id": claim_id}
    ).sort("seq", ASCENDING):
        out.append({
            "seq": r["seq"], "event_id": r["event_id"],
            "ts": r["ts"], "kind": r["kind"],
            "summary": r["summary"], "mocked": bool(r.get("mocked", False)),
        })
    return out
