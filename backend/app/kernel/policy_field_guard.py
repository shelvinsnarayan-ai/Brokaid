"""Made-up-field guard (R0.2).

Validates LLM-proposed `requested_changes` against the policy's current
schedule. Anything the policy doesn't actually hold raises
`UnmatchedPolicyField` — the caller parks the envelope for broker review.
Never auto-fixes or drops a change.
"""
from __future__ import annotations

import re
from typing import Any


class UnmatchedPolicyField(ValueError):
    def __init__(self, field_path: str, attempted: Any, reason: str):
        self.field_path = field_path
        self.attempted = attempted
        self.reason = reason
        super().__init__(f"{field_path}: {reason}")

    def as_payload(self) -> dict[str, Any]:
        return {"field_path": self.field_path, "attempted_value": self.attempted,
                "reason": self.reason}


def _norm(v: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(v or "").upper())


def _slug(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "_")


_ALIASES = {
    "driver_id": ("driver_id",),
    "rego": ("rego", "registration", "registration_number", "plate"),
    "vin": ("vin",),
    "licence_number": ("licence_number", "license_number", "licence"),
    "name": ("name", "driver_name"),
}


def _ident(value: Any, keys: tuple[str, ...]) -> list[str]:
    if isinstance(value, dict):
        return [_norm(value.get(a)) for k in keys for a in _ALIASES.get(k, (k,)) if value.get(a)]
    return [_norm(value)] if value else []


def _check_listed(schedule: dict, list_key: str, ch: dict, keys: tuple[str, ...]) -> None:
    fp = ch.get("field_path") or ""
    op = (ch.get("operation") or ch.get("op") or "").lower()
    items = schedule.get(list_key)
    if not isinstance(items, list) or not items:
        raise UnmatchedPolicyField(
            fp, ch.get("from_value") or ch.get("to_value"),
            f"policy has no schedule.{list_key}",
        )
    if op == "add":
        return
    ref = ch.get("from_value") if ch.get("from_value") is not None else ch.get("to_value")
    wanted = set(_ident(ref, keys))
    held = {i for item in items for i in _ident(item, keys)}
    if not wanted and ref is not None:
        # No identifier field — accept only if a held identifier appears verbatim in the text.
        text = _norm(" ".join(str(v) for v in (ref.values() if isinstance(ref, dict) else [ref])))
        wanted = {h for h in held if h and h in text}
    if not wanted or not (wanted & held):
        raise UnmatchedPolicyField(fp, ref, f"no matching entry in schedule.{list_key}")


def _check_address(schedule: dict, ch: dict) -> None:
    fp = ch.get("field_path") or ""
    held = {_norm(a.get("address") if isinstance(a, dict) else a)
            for a in schedule.get("addresses") or []}
    held |= {_norm(v.get("garaged_address")) for v in schedule.get("vehicles") or []
             if isinstance(v, dict) and v.get("garaged_address")}
    held.discard("")
    ref = ch.get("from_value")
    if not held:
        raise UnmatchedPolicyField(fp, ref or ch.get("to_value"), "policy holds no addresses")
    if ref is not None:
        want = _norm(ref.get("address") if isinstance(ref, dict) else ref)
        # R1.7: a client naming just the street ("4 Slipway Lane") matches if exactly one held address starts with it
        if want not in held and not (len(want) >= 6 and sum(1 for h in held if h.startswith(want)) == 1):
            raise UnmatchedPolicyField(fp, ref, "address not on the policy schedule")


def _check_limit(schedule: dict, ch: dict) -> None:
    fp = ch.get("field_path") or ""
    slug = fp.split("limits.", 1)[1].split(".")[0]
    covers = {_slug(c.get("type") or c.get("slug") or "")
              for c in schedule.get("cover_types") or []}
    if slug not in covers:
        raise UnmatchedPolicyField(fp, ch.get("to_value"), f"no cover_types entry for {slug!r}")


_ELEMENT = re.compile(r"^schedule\.(vehicles|drivers)\[([^\]]+)\]\.(.+)$")
_KEYS = {"vehicles": ("rego", "vin"), "drivers": ("licence_number", "name", "driver_id")}


def _check_element(schedule: dict, ch: dict, m: re.Match) -> None:
    list_key, ref, field = m.group(1), m.group(2), m.group(3)
    items = schedule.get(list_key) or []
    fp = ch.get("field_path") or ""
    if ref.isdigit():
        if int(ref) >= len(items):
            raise UnmatchedPolicyField(fp, ref, f"no schedule.{list_key}[{ref}]")
    elif _norm(ref) not in {i for it in items for i in _ident(it, _KEYS[list_key])}:
        raise UnmatchedPolicyField(fp, ref, f"no matching entry in schedule.{list_key}")
    if "address" in field:
        _check_address(schedule, ch)


def validate_requested_changes(policy: dict, changes: list[dict] | None) -> None:
    schedule = (policy or {}).get("schedule") or {}
    for ch in changes or []:
        fp = ch.get("field_path") or ""
        if (m := _ELEMENT.match(fp)):
            _check_element(schedule, ch, m)
        elif fp.startswith("schedule.vehicles"):
            _check_listed(schedule, "vehicles", ch, ("rego", "vin"))
        elif fp.startswith("schedule.drivers"):
            _check_listed(schedule, "drivers", ch, ("licence_number", "name"))
        elif "address" in fp:
            _check_address(schedule, ch)
        elif "limits." in fp:
            _check_limit(schedule, ch)


_REGO_FIELDS = {"registration", "rego", "registration_number"}


def _resolve_by_description(policy: dict, changes: list[dict]) -> list[dict]:
    """R1.7 eval fix: 'take the Navara off' → a remove naming make/model but no rego. Fill the rego only when exactly one
    held vehicle matches every given descriptor; otherwise leave it so the guard parks it."""
    held = ((policy or {}).get("schedule") or {}).get("vehicles") or []
    out = []
    for ch in changes:
        fv = ch.get("from_value")
        if (ch.get("field_path") == "schedule.vehicles[]" and (ch.get("operation") or "").lower() == "remove"
                and isinstance(fv, dict) and not any(fv.get(a) for a in ("rego", "registration", "vin"))):
            desc = {k: str(fv[k]).strip().lower() for k in ("make", "model", "year", "body_type") if fv.get(k)}
            hits = [v for v in held if desc and all(d in str(v.get(k, "")).lower() for k, d in desc.items())]
            if len(hits) == 1:
                ch = {**ch, "from_value": {**fv, "rego": hits[0]["rego"]}}
        out.append(ch)
    return out


def normalize_vehicle_ops(policy: dict, changes: list[dict] | None) -> list[dict]:
    """R0.11 follow-up: fold per-vehicle element paths into the canonical list-level ops.

    Models sometimes write a swap as `schedule.vehicles[NEW].field` sets (plus an edit on the held
    vehicle) instead of `schedule.vehicles[]` remove + add. Fold only when the request removes or
    re-registers a held vehicle, or explicitly adds; a lone edit to an unknown vehicle is left as is
    so the made-up-field guard still parks it.
    """
    if not changes:
        return changes or []
    changes = _resolve_by_description(policy, changes)
    held = {i for v in ((policy or {}).get("schedule") or {}).get("vehicles") or [] for i in _ident(v, _KEYS["vehicles"])}
    keep, removes, new, explicit_add = [], [], {}, False
    for ch in changes:
        m = _ELEMENT.match(ch.get("field_path") or "")
        if not m or m.group(1) != "vehicles" or m.group(2).isdigit():
            keep.append(ch)
            continue
        ref, field, op = m.group(2), m.group(3), (ch.get("operation") or "").lower()
        if _norm(ref) in held:
            to = ch.get("to_value")
            if op == "remove" or (field in _REGO_FIELDS and to and _norm(to) not in held):
                removes.append(ref)
                if op != "remove" and to:
                    new.setdefault(_norm(to), {"rego": str(to)})
            else:
                keep.append(ch)
            continue
        if op in ("add", "set"):
            explicit_add = explicit_add or op == "add"
            veh = new.setdefault(_norm(ref), {"rego": ref})
            if field not in _REGO_FIELDS:
                veh[field] = ch.get("to_value")
        else:
            keep.append(ch)
    if not (removes or explicit_add) or not new:
        return changes
    cite = {"kind": "policy_schedule", "policy_id": None, "field_path": "schedule.vehicles[]"}
    return keep + [
        {"field_path": "schedule.vehicles[]", "operation": "remove", "from_value": {"rego": r},
         "to_value": None, "citation": cite} for r in dict.fromkeys(removes)
    ] + [
        {"field_path": "schedule.vehicles[]", "operation": "add", "from_value": None,
         "to_value": v, "citation": cite} for v in new.values()
    ]
