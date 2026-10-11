"""R0.5 — kill switches by agent / package / global × tenant (thin Gate 0).
# tenant-safe: system-only  (kill_switches rows may be tenant-less = all tenants)

Enabled rows block matching Brain.invoke calls before any model call.
In-process cache with a 10 s TTL caps propagation at 10 s (target <60 s);
engage/release in this process invalidate the cache immediately.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.tenant_safe import TenantSafeDatabase

CACHE_TTL_S = 10.0
_cache: dict[str, Any] = {"at": 0.0, "rows": []}

AGENT_PACKAGES: dict[str, str] = {
    "intake": "spine", "placement": "spine",
    "claims": "claims", "compliance": "compliance",
    "liaison": "client_comms", "notify": "client_comms", "docs": "client_comms",
    "certs": "client_comms",
    "finance": "back_office", "retention": "back_office",
    "terrain": "back_office", "campaign": "back_office", "mock": "back_office",
}
# Plain-language labels for screens (no internal agent names on UI).
AGENT_LABELS = {
    "intake": "Request intake", "placement": "Placement drafting",
    "claims": "Claims triage", "compliance": "Compliance review",
    "liaison": "Client answers", "notify": "Notifications", "docs": "Documents",
    "certs": "Certificates", "finance": "Finance", "retention": "Renewals",
    "terrain": "Market data", "campaign": "Campaigns", "mock": "Test agent",
}
PACKAGE_LABELS = {
    "spine": "Change requests (intake + placement)", "claims": "Claims",
    "compliance": "Compliance", "client_comms": "Client communications",
    "back_office": "Back office",
}


class KillSwitchActive(Exception):
    def __init__(self, row: dict, agent_name: str):
        self.row, self.agent_name = row, agent_name
        super().__init__(f"kill switch {row['switch_id']} active for {agent_name}")

    def as_payload(self) -> dict:
        return {"code": "KillSwitchActive", "switch_id": self.row["switch_id"],
                "scope_kind": self.row["scope_kind"], "scope_ref": self.row.get("scope_ref"),
                "reason": self.row.get("reason")}


def raw_db(store):
    return store._db if isinstance(store, TenantSafeDatabase) else store


def invalidate() -> None:
    _cache["at"] = 0.0


async def _enabled_rows(db) -> list[dict]:
    now = time.monotonic()
    if now - _cache["at"] > CACHE_TTL_S:
        _cache["rows"] = [r async for r in db.kill_switches.find({"enabled": True}, {"_id": 0})]
        _cache["at"] = now
    return _cache["rows"]


def match(rows: list[dict], agent_name: str, tenant_id: str) -> dict | None:
    pkg = AGENT_PACKAGES.get(agent_name)
    for r in rows:
        if r.get("tenant_id") not in (None, tenant_id):
            continue
        kind, ref = r.get("scope_kind"), r.get("scope_ref")
        if kind == "global" or (kind == "agent" and ref == agent_name) or (
                kind == "package" and ref == pkg):
            return r
    return None


async def check(store, agent_name: str, tenant_id: str) -> dict | None:
    return match(await _enabled_rows(raw_db(store)), agent_name, tenant_id)


async def engage(store, *, scope_kind: str, scope_ref: str | None, tenant_id: str | None,
                 reason: str, user_id: str) -> dict:
    row = {"switch_id": str(uuid.uuid4()), "scope_kind": scope_kind,
           "scope_ref": None if scope_kind == "global" else scope_ref,
           "tenant_id": tenant_id or None, "enabled": True, "enabled_by": user_id,
           "enabled_at": datetime.now(timezone.utc), "reason": reason,
           "released_at": None, "released_by": None, "release_reason": None}
    await raw_db(store).kill_switches.insert_one(dict(row))
    invalidate()
    return row


async def release(store, *, switch_id: str, reason: str, user_id: str) -> dict | None:
    db = raw_db(store)
    res = await db.kill_switches.update_one(
        {"switch_id": switch_id, "enabled": True},
        {"$set": {"enabled": False, "released_at": datetime.now(timezone.utc),
                  "released_by": user_id, "release_reason": reason}})
    invalidate()
    if not res.modified_count:
        return None
    return await db.kill_switches.find_one({"switch_id": switch_id}, {"_id": 0})


async def list_all(store) -> list[dict]:
    return [r async for r in raw_db(store).kill_switches.find({}, {"_id": 0}).sort("enabled_at", -1)]
