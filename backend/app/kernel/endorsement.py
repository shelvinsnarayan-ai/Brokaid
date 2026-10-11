"""Phase R5b-1a — endorsement kernel (dispatch-aware).

Extract-first (byte-verbatim) — this module keeps its inline
Postgres SQL for the PG branch (same shape as pre-extraction:
`SELECT set_config('app.tenant_id', $1, true)` under a transaction,
same `SELECT schedule FROM policies` lookup, same `UPDATE policies
SET schedule=$3::jsonb` write-back atomic to the read). Under
Mongo, all reads/writes delegate to `kernel.endorsement_mongo`.

The `_load_lineage` / `_apply_change` pure-shape helpers are kept
unchanged from the pre-extraction implementation. `execute_endorsement`
is the only externally-consumed entrypoint and now takes `store=`
instead of the old `pool=` — callers under `BROKAID_LEDGER=mongo`
must pass a Motor DB or `TenantSafeDatabase`; other callers pass
the pool exactly as before.

Byte-identical event hashes across backends for identical
`_load_lineage` output + identical `ts_iso` (production paths do
not override ts, so parity tests set the same `ts_override` on
both stores directly against `trail.append` / `trail_mongo.append_event`
per the R5a-2b-α scanner allow-list).
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.tenant_safe import TenantSafeDatabase
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch


class EndorsementLineageIncomplete(RuntimeError):
    """Fired if the required upstream events aren't all present."""


def _is_mongo(store) -> bool:
    return isinstance(store, (AsyncIOMotorDatabase, TenantSafeDatabase))


async def _load_lineage(
    store, tenant_id: str, envelope_id: str,
) -> dict[str, Any]:
    if _is_mongo(store):
        from app.kernel import endorsement_mongo
        return await endorsement_mongo.load_lineage(
            store, tenant_id, envelope_id,
        )
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            rows = await c.fetch(
                """
                SELECT type, payload FROM events
                 WHERE tenant_id=$1::uuid
                   AND payload->>'envelope_id' = $2
                 ORDER BY seq ASC
                """,
                tenant_id, envelope_id,
            )
    lineage: dict[str, Any] = {}
    for r in rows:
        p = json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
        lineage.setdefault(r["type"], p)   # first occurrence
    return lineage


def _slug(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "_")


def _sync_cover_types_to_limits(schedule: dict[str, Any]) -> None:
    """Mirror `schedule.limits.<slug>` into any matching
    `schedule.cover_types[*].limit_any_one`.

    The policy seed stores two parallel representations: a flat
    `limits` map that endorsements write to, and a `cover_types` list
    that the certificate PDF + verifier render. Without this mirror
    the certificate shows the pre-endorsement limits forever.
    R1 close-out (user prod verification 2026-10-02).
    """
    limits = schedule.get("limits")
    cover_types = schedule.get("cover_types")
    if not isinstance(limits, dict) or not isinstance(cover_types, list):
        return
    for ct in cover_types:
        if not isinstance(ct, dict):
            continue
        key = _slug(ct.get("type"))
        if key in limits:
            try:
                ct["limit_any_one"] = int(limits[key])
            except (TypeError, ValueError):
                ct["limit_any_one"] = limits[key]


_REF_KEYS = {
    "vehicles": ("rego", "registration", "registration_number", "plate", "vin"),
    "drivers": ("driver_id", "licence_number", "license_number", "name"),
}
_ELEMENT_PATH = re.compile(r"^(?:schedule\.)?(vehicles|drivers)\[([^\]]*)\]\.?(.*)$")


def _norm_ref(v: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(v).upper())


def _list_refs(v: Any, list_key: str = "vehicles") -> set[str]:
    if isinstance(v, dict):
        return {_norm_ref(v[k]) for k in _REF_KEYS[list_key] if v.get(k)}
    return {_norm_ref(v)} if v else set()


def _vehicle_refs(v: Any) -> set[str]:
    return _list_refs(v, "vehicles")


def _normalise_vehicle(v: Any) -> dict[str, Any]:
    if not isinstance(v, dict):
        return {"rego": str(v)}
    out = dict(v)
    for k in ("registration", "registration_number", "plate"):
        if k in out and "rego" not in out:
            out["rego"] = out.pop(k)
    return out


def _apply_list_change(schedule: dict[str, Any], change: dict[str, Any]) -> None:
    """vehicles/drivers: add appends · remove drops the match · replace = atomic
    swap · `[<ref>].<field>` set updates that element's field."""
    m = _ELEMENT_PATH.match(change.get("field_path") or "")
    list_key, elem_ref, field = m.group(1), m.group(2), m.group(3)
    items = schedule.setdefault(list_key, [])
    op = (change.get("operation") or "set").lower()
    norm = _normalise_vehicle if list_key == "vehicles" else (lambda v: dict(v) if isinstance(v, dict) else {"name": str(v)})
    if elem_ref and field:
        idx = int(elem_ref) if elem_ref.isdigit() else next(
            (i for i, it in enumerate(items) if _norm_ref(elem_ref) in _list_refs(it, list_key)), None)
        if idx is None or idx >= len(items):
            raise EndorsementLineageIncomplete(f"{list_key}[{elem_ref}] not on policy schedule")
        items[idx][field] = change.get("to_value")
        return
    if op in ("remove", "replace"):
        ref = change.get("from_value")
        if ref is None and op == "remove":
            ref = change.get("to_value")
        refs = _list_refs(ref, list_key)
        kept = [it for it in items if not (_list_refs(it, list_key) & refs)]
        if not refs or len(kept) == len(items):
            raise EndorsementLineageIncomplete(f"{list_key} entry {ref!r} not on policy schedule")
        items[:] = kept
    if op in ("add", "replace"):
        new = norm(change.get("to_value"))
        if not any(_list_refs(it, list_key) & _list_refs(new, list_key) for it in items):
            items.append(new)


def vehicle_mutations(changes: list[dict[str, Any]], schedule_after: dict[str, Any]) -> dict[str, Any]:
    added, removed = [], []
    for ch in changes:
        m = _ELEMENT_PATH.match(ch.get("field_path") or "")
        if not m or m.group(1) != "vehicles" or m.group(3):
            continue
        op = (ch.get("operation") or "").lower()
        if op in ("remove", "replace"):
            ref = ch.get("from_value") if ch.get("from_value") is not None else ch.get("to_value")
            removed.append(_normalise_vehicle(ref).get("rego") or _normalise_vehicle(ref).get("vin"))
        if op in ("add", "replace"):
            added.append(_normalise_vehicle(ch.get("to_value")).get("rego"))
    if not added and not removed:
        return {}
    return {"added": added, "removed": removed,
            "vehicles_after": [v.get("rego") for v in schedule_after.get("vehicles") or []]}


def _apply_change(schedule: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    """Naïve dot-path setter used by the mocked scene. Applies a
    `field_path` + `to_value` write into the policy schedule.

    Verbatim from the pre-extraction implementation — see the R5a-2b-β
    extraction discipline: same behaviour end-to-end.
    """
    raw = change.get("field_path") or ""
    if _ELEMENT_PATH.match(raw):
        _apply_list_change(schedule, change)
        return schedule
    if raw.startswith("schedule."):
        raw = raw[len("schedule."):]
    path = raw.split(".") if raw else []
    op = change.get("operation") or "set"
    if not path or path == [""]:
        return schedule
    cur = schedule
    for p in path[:-1]:
        cur = cur.setdefault(p, {})
        if not isinstance(cur, dict):
            return schedule
    last = path[-1]
    if op == "set" or op == "increase" or op == "decrease":
        cur[last] = change.get("to_value")
    elif op == "add":
        arr = cur.setdefault(last, [])
        if isinstance(arr, list):
            arr.append(change.get("to_value"))
    elif op == "remove":
        cur.pop(last, None)
    _sync_cover_types_to_limits(schedule)
    return schedule


async def _rmw_policy_schedule(
    store, tenant_id: str, policy_id: str, changes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Atomic read-modify-write of policies.schedule. On PG this
    happens inside a single txn (verbatim from the pre-extraction
    code). On Mongo it's a single-document find_one + update_one —
    non-atomic across processes but sufficient for the scene, which
    serialises per-policy on the operator queue.
    """
    if _is_mongo(store):
        from app.kernel import endorsement_mongo
        return await endorsement_mongo.rmw_policy_schedule(
            store, tenant_id, policy_id, changes,
        )
    async with store.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id,
            )
            row = await c.fetchrow(
                "SELECT schedule FROM policies "
                "WHERE tenant_id=$1::uuid AND policy_id=$2::uuid",
                tenant_id, policy_id,
            )
            if row is None:
                raise EndorsementLineageIncomplete(
                    f"policy {policy_id} not found"
                )
            schedule = (
                json.loads(row["schedule"])
                if isinstance(row["schedule"], str)
                else dict(row["schedule"])
            )
            for ch in changes:
                _apply_change(schedule, ch)
            await c.execute(
                "UPDATE policies SET schedule=$3::jsonb "
                "WHERE tenant_id=$1::uuid AND policy_id=$2::uuid",
                tenant_id, policy_id, json.dumps(schedule),
            )
    return schedule


async def _emit(store, tenant_id: str, **kw) -> dict[str, Any]:
    if _is_mongo(store):
        async with use_tenant(tenant_id):
            return await append_via_dispatch(store, tenant_id, **kw)
    return await append_via_dispatch(store, tenant_id, **kw)


async def execute_endorsement(
    *,
    store,
    tenant_id: str,
    envelope_id: str,
    policy_id: str,
    approver_actor: dict[str, Any],
) -> dict[str, Any]:
    """Run after Approval + payment.advised + SignatureCompleted.

    Emits `EndorsementExecuted` + `PolicyUpdated`, updates the
    policies row.  Returns the endorsement payload dict.

    Dispatch-aware since R5b-1a: `store` is either an pool
    (Postgres) or a Motor DB / `TenantSafeDatabase` (Mongo). Same
    event authorship, same payload keyset, same idempotency keys,
    byte-identical event_hash for identical inputs.
    """
    lineage = await _load_lineage(store, tenant_id, envelope_id)
    for required in ("Approval", "payment.advised", "SignatureCompleted"):
        if required not in lineage:
            raise EndorsementLineageIncomplete(
                f"missing upstream event: {required}"
            )

    submission = lineage.get("SubmissionDrafted") or {}
    structured = submission.get("structured_submission") or {}
    requested_changes = (
        structured.get("requested_changes")
        or submission.get("requested_changes")
        or []
    )

    endorsement_id = str(uuid.uuid4())
    effective_date = datetime.now(timezone.utc).isoformat(timespec="microseconds")

    schedule = await _rmw_policy_schedule(
        store, tenant_id, policy_id, requested_changes,
    )

    await _emit(
        store, tenant_id, type="EndorsementExecuted",
        actor=approver_actor,
        payload={
            "envelope_id": envelope_id,
            "endorsement_id": endorsement_id,
            "policy_id": policy_id,
            "changes_applied": requested_changes,
            "effective_date": effective_date,
            "mocked_channels": ["carrier", "payment", "e_sign"],
        },
        idempotency_key=f"endorsement:exec:{envelope_id}",
    )
    await _emit(
        store, tenant_id, type="PolicyUpdated",
        actor={"kind": "system", "id": "endorsement", "name": "endorsement"},
        payload={
            "envelope_id": envelope_id,
            "policy_id": policy_id,
            "schedule_after": schedule,
            "endorsement_id": endorsement_id,
            **({"vehicle_mutations": vm} if (vm := vehicle_mutations(requested_changes, schedule)) else {}),
        },
        idempotency_key=f"policy:updated:{envelope_id}",
    )
    return {
        "endorsement_id": endorsement_id,
        "policy_id": policy_id,
        "changes_applied": requested_changes,
        "effective_date": effective_date,
        "schedule_after": schedule,
    }
