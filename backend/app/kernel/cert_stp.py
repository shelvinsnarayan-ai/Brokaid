"""R-CoC-01 — certificate self-serve STP gate (R0.4).

All five pass → instant certificate. Any fail → broker task (never an error).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant

STANDARD_WORDING = re.compile(r"additional insured|loss payee|interested party", re.I)
NON_STANDARD = re.compile(r"\b(override|custom|subject to|waiver|hold harmless|primary and non.?contributory)\b", re.I)
OPEN_CLAIM_STATES = {"lodged", "open", "assessing", "needs_you", "decision", "settling"}

PLAIN = {
    "policy_not_in_force": "This policy isn't currently in force.",
    "premium_overdue": "There's an overdue premium on this policy.",
    "limits_changed_recently": "The cover limits changed in the last 30 days.",
    "open_claim": "There's an open claim on this policy.",
    "non_standard_wording": "The certificate wording asked for isn't standard.",
}


def wording_is_non_standard(special: str | None) -> bool:
    return bool(special) and bool(NON_STANDARD.search(special))


def evaluate(policy: dict, *, recent_limit_change: bool, open_claim: bool,
             special: str | None) -> list[str]:
    reasons = []
    if policy.get("status") not in ("in_force", "managed"):
        reasons.append("policy_not_in_force")
    if (policy.get("premium_status") or "current") == "overdue":
        reasons.append("premium_overdue")
    if recent_limit_change:
        reasons.append("limits_changed_recently")
    if open_claim:
        reasons.append("open_claim")
    if wording_is_non_standard(special):
        reasons.append("non_standard_wording")
    return reasons


async def facts(store, tenant_id: str, policy_id: str) -> dict[str, Any]:
    safe = wrap(store)
    since = datetime.now(timezone.utc) - timedelta(days=30)
    async with use_tenant(tenant_id):
        recent = False
        async for ev in safe.events.find({"type": "PolicyUpdated", "payload.policy_id": policy_id}):
            ts = ev.get("ts")
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00")) if isinstance(ts, str) else ts
            if ts and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            changes = (ev.get("payload") or {}).get("changes_applied") or []
            if ts and ts >= since and any("limits." in (c.get("field_path") or "") for c in changes):
                recent = True
        claim = await safe.claims.find_one(
            {"policy_id": policy_id, "status": {"$in": sorted(OPEN_CLAIM_STATES)}})
    return {"recent_limit_change": recent, "open_claim": claim is not None}
