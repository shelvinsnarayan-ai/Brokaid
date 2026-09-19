"""Phase R4c — Mongo mirrors of the four projections that are written
OUTSIDE `trail.py::_project` (i.e. app-driven from route/kernel
callers rather than the append-transaction). Same in-line refresh
discipline as R4b's trigger audit finding.

Covers:
  proj_notifications
  proj_kpi_daily
  proj_class_unlock
  proj_claim_status_updates (single-shot writer — see also
    `claims_mongo.append_status_update`)

Every function is a pure Mongo writer scoped by the current
`TenantSafeDatabase` context; no side effects on Postgres.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from pymongo import ASCENDING

from app.db.tenant_safe import TenantSafeDatabase, wrap
from app.kernel.tenant_ctx import get_tenant_id


_indexes_initialised: set[int] = set()


async def _ensure_indexes(safe_db: TenantSafeDatabase) -> None:
    raw = safe_db.raw
    key = id(raw)
    if key in _indexes_initialised:
        return
    await raw["proj_notifications"].create_index(
        [("tenant_id", ASCENDING), ("notification_id", ASCENDING)],
        unique=True, name="pn_tenant_notif_uniq",
    )
    await raw["proj_notifications"].create_index(
        [("tenant_id", ASCENDING), ("client_id", ASCENDING), ("ts", ASCENDING)],
        name="pn_tenant_client_ts",
    )
    await raw["proj_kpi_daily"].create_index(
        [("tenant_id", ASCENDING), ("day", ASCENDING), ("kpi_id", ASCENDING)],
        unique=True, name="pkd_tenant_day_kpi",
    )
    await raw["proj_class_unlock"].create_index(
        [("tenant_id", ASCENDING), ("class_key", ASCENDING)],
        unique=True, name="pcu_tenant_class",
    )
    _indexes_initialised.add(key)


# ---- proj_notifications ---------------------------------------------
async def upsert_notification(
    db, *, notification_id: str, client_id: str,
    source_event_id: str, ts: datetime,
    kind: str, subject: str, body_text: str,
) -> None:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    get_tenant_id()
    doc = {
        "notification_id": notification_id,
        "client_id": client_id,
        "source_event_id": source_event_id,
        "ts": ts, "kind": kind,
        "subject": subject, "body_text": body_text,
        "read_at": None,
    }
    await safe_db.proj_notifications.update_one(
        {"notification_id": notification_id},
        {"$setOnInsert": doc},
        upsert=True,
    )


# ---- proj_kpi_daily -------------------------------------------------
async def upsert_kpi_daily(
    db, *, day: date, kpi_id: int, value_scalar: float,
    value_meta: Optional[dict[str, Any]] = None,
    delta_prev: Optional[float] = None,
    spark_series: Optional[list[float]] = None,
) -> None:
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    get_tenant_id()
    # Store day as ISO date string for stable JSON serialisation.
    day_iso = day.isoformat() if isinstance(day, date) else str(day)
    await safe_db.proj_kpi_daily.update_one(
        {"day": day_iso, "kpi_id": int(kpi_id)},
        {"$set": {
            "value_scalar": float(value_scalar),
            "value_meta": value_meta or {},
            "delta_prev": delta_prev,
            "spark_series": spark_series or [],
            "computed_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )


# ---- proj_class_unlock ----------------------------------------------
UNLOCK_THRESHOLD_DECISIONS = 50
UNLOCK_THRESHOLD_AGREEMENT = 0.95


async def apply_class_unlock_decision(
    db, *, class_key: str,
    agreed_with_agent: bool, eno_flag: bool, event_id: str,
) -> tuple[bool, Optional[str]]:
    """Mirror of `gate.unlock.apply_human_decision` for Mongo. Returns
    (state_changed, new_state_kind) — 'ClassUnlocked' | 'ClassRelocked'
    | None."""
    safe_db = wrap(db)
    await _ensure_indexes(safe_db)
    get_tenant_id()

    row = await safe_db.proj_class_unlock.find_one({"class_key": class_key})
    if row is None:
        row = {
            "class_key": class_key, "decisions_seen": 0,
            "agreements": 0, "eno_misses": 0, "unlocked": False,
            "unlocked_at": None, "last_relock_at": None,
            "last_relock_reason": None,
        }
        await safe_db.proj_class_unlock.update_one(
            {"class_key": class_key},
            {"$setOnInsert": {**row, "updated_by_event": event_id}},
            upsert=True,
        )

    decisions_seen = int(row.get("decisions_seen", 0)) + 1
    agreements = int(row.get("agreements", 0)) + (1 if agreed_with_agent else 0)
    eno_misses = int(row.get("eno_misses", 0)) + (
        1 if (not agreed_with_agent and eno_flag) else 0
    )
    prev_unlocked = bool(row.get("unlocked", False))
    relock = (not agreed_with_agent) and eno_flag
    if relock:
        unlocked = False
    else:
        rate = agreements / decisions_seen if decisions_seen else 0.0
        unlocked = (
            decisions_seen >= UNLOCK_THRESHOLD_DECISIONS
            and rate >= UNLOCK_THRESHOLD_AGREEMENT
            and eno_misses == 0
        )

    now = datetime.now(timezone.utc)
    set_fields = {
        "decisions_seen": decisions_seen,
        "agreements": agreements,
        "eno_misses": eno_misses,
        "unlocked": unlocked,
        "updated_by_event": event_id,
    }
    if unlocked and not prev_unlocked:
        set_fields["unlocked_at"] = now
    if relock and prev_unlocked:
        set_fields["last_relock_at"] = now
        set_fields["last_relock_reason"] = "eno_flag miss"
    await safe_db.proj_class_unlock.update_one(
        {"class_key": class_key},
        {"$set": set_fields},
    )

    if unlocked and not prev_unlocked:
        return True, "ClassUnlocked"
    if relock and prev_unlocked:
        return True, "ClassRelocked"
    return False, None
