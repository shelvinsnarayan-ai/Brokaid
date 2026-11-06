"""R3.15: "Handled by" card for client matters: the named AE (assignment, falling back to the Principal) and the next step.
Never invents dates: `by` only comes from a real due date passed in by the caller; otherwise the line is omitted."""
from __future__ import annotations

from datetime import datetime

from app.db.tenant_safe import wrap
from app.kernel.notify import _staff, assigned_ae
from app.kernel.tenant_ctx import use_tenant

ROLE_LABEL = {"ae": "Account executive", "principal": "Principal"}


def initials(name: str | None) -> str:
    parts = [p for p in (name or "").replace(".", " ").split() if p[:1].isalpha()]
    return "".join(p[0] for p in parts[:2]).upper() or "B"


def au_date(v) -> str | None:
    if not v:
        return None
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    from zoneinfo import ZoneInfo
    if v.tzinfo is None:
        from datetime import timezone
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(ZoneInfo("Australia/Sydney")).strftime("%d/%m/%Y")


async def card(store, tid: str, client_id: str | None, *, next_step: str | None = None, by=None,
               update_by=None) -> dict:
    """{name, role, initials, photo_url, next_step?, by?, update_by?}. `update_by` → "We'll update you by <date>" when no step date."""
    safe = wrap(store)
    async with use_tenant(tid):
        uid, role = await assigned_ae(safe, client_id), "ae"
        if not uid:
            uid, role = next(iter(await _staff(safe, ("principal",))), None), "principal"
        u = await safe.users.find_one({"user_id": uid}, {"display_name": 1, "photo_url": 1}) if uid else None
    name = (u or {}).get("display_name") or "Your broker"
    out = {"name": name, "role": ROLE_LABEL.get(role, "Your broker") if u else "Your broker", "initials": initials(name),
           "photo_url": (u or {}).get("photo_url")}
    if next_step:
        out["next_step"] = next_step
        if au_date(by):
            out["by"] = au_date(by)
        elif au_date(update_by):
            out["update_by"] = au_date(update_by)
    return out


CLAIM_STEP = {"lodged": "We acknowledge your claim", "triage": "Your broker reviews your claim",
              "info_requested": "Send us what we asked for", "submitted": "The insurer reviews your claim",
              "assessing": "The insurer's assessment", "decision": "We explain the insurer's decision",
              "settlement": "Settlement is arranged"}


def claim_next(c: dict) -> tuple[str | None, object]:
    """(plain step, real due date or None) from the claim's own stage, open info requests and acknowledgement SLA."""
    st = c.get("stage")
    if st in ("closed", "withdrawn"):
        return None, None
    req = next((r for r in c.get("info_requests") or [] if r.get("status") == "open"), None)
    if req:
        return "Send us what we asked for", req.get("due")
    if st == "lodged" and not c.get("ack_at"):
        return CLAIM_STEP["lodged"], c.get("ack_due")
    return CLAIM_STEP.get(st), None
