"""R0.6 — Things content model (Addendum 2 §4.1).

"For" sentences come from per-class templates over schedule fields.
Never model-generated.
"""
from __future__ import annotations

from datetime import date, datetime

GROUPS = ("vehicle", "place", "business", "person", "travel", "not_covered")
STATUSES = ("covered", "covered_conditional", "held_elsewhere", "not_covered")


def _money(v) -> str:
    v = float(v)
    if v >= 1_000_000 and v % 1_000_000 == 0:
        return f"${int(v // 1_000_000)}m"
    return f"${v:,.0f}"


def _d(v) -> str:
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if isinstance(v, (datetime, date)):
        return f"{v.day} {v.strftime('%b %Y')}"
    return str(v)


def _motor(f: dict) -> str:
    return f"{f['cover_level']}. {f['who_can_drive']}; {f['restriction']}."


def _home(f: dict) -> str:
    return f"{f['cover_level']}. {f['flood']}; {f['occupancy']}."


def _liability(f: dict) -> str:
    return f"{_money(f['limit'])} liability. {f['where_what']}. {f['exclusion']}."


def _held_elsewhere(f: dict) -> str:
    return f"Held with {f['insurer']} — not arranged by us."


def _not_covered(f: dict) -> str:
    return f"Not insured. You told us about it on {_d(f['told_us_on'])}."


TEMPLATES = {
    "motor": _motor, "home": _home, "liability": _liability,
    "held_elsewhere": _held_elsewhere, "not_covered": _not_covered,
}


def for_sentence(template: str, fields: dict) -> str:
    return TEMPLATES[template](fields)


def numbers_for(template: str, item: dict, insurer: str | None) -> list[dict]:
    """Max three label/value pairs for the card, from schedule fields."""
    f = item.get("fields") or {}
    out: list[dict] = []
    if template == "motor":
        out = [{"label": "Agreed value", "value": _money(f["agreed_value"])},
               {"label": "Excess", "value": _money(f["excess"])}]
    elif template == "home":
        out = [{"label": "Building", "value": _money(f["building_sum"])},
               {"label": "Contents", "value": _money(f["contents_sum"])}]
    elif template == "liability":
        out = [{"label": "Limit", "value": _money(f["limit"])},
               {"label": "Excess", "value": _money(f["excess"])}]
    if insurer and template in TEMPLATES and template not in ("not_covered",):
        out.append({"label": "Insurer", "value": insurer})
    return out[:3]


def render(thing: dict, policies: dict[str, dict]) -> dict:
    """Card view of a thing; every value comes from the thing + linked policies."""
    items = thing.get("schedule_items") or []
    first = items[0] if items else {}
    pol = policies.get(first.get("policy_id")) if first else None
    template = thing.get("template") or "not_covered"
    fields = {**(first.get("fields") or {}), **(thing.get("template_fields") or {})}
    if template == "not_covered":
        fields.setdefault("told_us_on", thing.get("told_us_on"))
    if template == "held_elsewhere":
        fields.setdefault("insurer", (pol or {}).get("insurer_display") or "another insurer")
    status = thing["status"]
    until = (pol or {}).get("expiry")
    status_sentence = {
        "covered": f"Covered until {_d(until)}" if until else "Covered",
        "covered_conditional": f"Covered with conditions until {_d(until)}" if until else "Covered with conditions",
        "held_elsewhere": "Held elsewhere",
        "not_covered": "Not covered",
    }[status]
    return {
        "thing_id": thing["thing_id"], "group": thing["group"], "name": thing["name"],
        "identifiers": thing.get("identifiers") or {}, "status": status,
        "status_sentence": status_sentence,
        "until": until.isoformat() if hasattr(until, "isoformat") else until,
        "for_sentence": for_sentence(template, fields),
        "numbers": numbers_for(template, first, (pol or {}).get("insurer_display")),
        "policy_ids": [i["policy_id"] for i in items],
        "product": (pol or {}).get("product"),
    }
