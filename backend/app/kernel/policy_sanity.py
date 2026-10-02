"""Startup sanity check: warn if a policy's `schedule.limits.<slug>`
drifts from its authoritative `schedule.cover_types[*].limit_any_one`.

Does NOT block startup. The idea is to surface a loud WARNING log for
operators so they can re-seed or run a correction endorsement.
"""
from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger("brokaid.api.policy_sanity")


def _slug(s: str | None) -> str:
    return (s or "").strip().lower().replace(" ", "_")


def _as_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _audit_schedule(schedule: dict[str, Any]) -> list[str]:
    """Return a list of mismatch descriptions. Empty = clean."""
    out: list[str] = []
    if not isinstance(schedule, dict):
        return out
    limits = schedule.get("limits")
    cover_types = schedule.get("cover_types")
    if not isinstance(limits, dict) or not isinstance(cover_types, list):
        return out
    cover_by_slug = {
        _slug(ct.get("type")): _as_int(ct.get("limit_any_one"))
        for ct in cover_types if isinstance(ct, dict)
    }
    for slug, lim in limits.items():
        if not isinstance(slug, str):
            continue
        lim_i = _as_int(lim)
        if lim_i is None:
            continue
        current = cover_by_slug.get(_slug(slug))
        if current is None:
            out.append(
                f"limits.{slug}={lim_i} but no cover_types entry for slug "
                f"{slug!r}"
            )
        elif current != lim_i:
            out.append(
                f"limits.{slug}={lim_i} does not match "
                f"cover_types[{slug}].limit_any_one={current}"
            )
    return out


async def warn_on_policy_schedule_mismatches(db) -> int:
    """Scan every policy across every tenant and emit a WARNING for
    each mismatch between `schedule.limits.<slug>` and the
    `schedule.cover_types[*].limit_any_one` display source.

    Returns the number of mismatched policies for test observability.
    `db` is either an `AsyncIOMotorDatabase` or `TenantSafeDatabase`.
    This is a system-level maintenance scan and intentionally uses the
    `.system(...)` escape hatch to cross tenants on startup.
    """
    from app.db.tenant_safe import TenantSafeDatabase

    if isinstance(db, TenantSafeDatabase):
        coll = db.system("policies")
    else:
        coll = db.policies
    count = 0
    async for row in coll.find({}, {"policy_id": 1, "tenant_id": 1,
                                     "schedule": 1}):
        mismatches = _audit_schedule(row.get("schedule") or {})
        if mismatches:
            count += 1
            logger.warning(
                "policy_schedule_limits_mismatch policy_id=%s tenant_id=%s "
                "mismatches=%s",
                row.get("policy_id"), row.get("tenant_id"), mismatches,
            )
    if count:
        logger.warning(
            "policy_sanity: %d policy(ies) have limits/cover_types drift. "
            "Clients will see pricing based on cover_types (authoritative).",
            count,
        )
    else:
        logger.info("policy_sanity: all policy schedules are consistent")
    return count
