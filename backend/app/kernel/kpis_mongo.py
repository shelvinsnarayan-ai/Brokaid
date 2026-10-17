"""Phase R5a-2a — Mongo mirror of the twelve KPI computations.

Each function mirrors `kpis.py::kpi_N_...` byte/numeric-identically
against Mongo collections. Same output shape, same field names, same
`not_yet_measurable` semantics. Parity test at
`tests/test_r5a2a_kpis_parity.py` asserts equality against a shared
seed.

Reads via `TenantSafeCollection` under `use_tenant(tenant_id)`.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.db.tenant_safe import wrap
from app.kernel.kpis import LABELS, KpiValue, _median, _parse_payload
from app.kernel.tenant_ctx import use_tenant
from app.kernel.test_data import real_policy_filter, test_client_ids, test_event_filter  # noqa: F401


async def _all_events(safe_db, types: list[str] | None = None):
    q: dict[str, Any] = await test_event_filter(safe_db)
    if types:
        q["type"] = {"$in": types}
    out = []
    async for r in safe_db.events.find(q).sort("seq", 1):
        out.append(r)
    return out


# ---- KPI 1: Book -----------------------------------------------------
async def kpi_1_book(safe_db, tenant_id: str) -> KpiValue:
    rows = []
    async for r in safe_db.policies.find({"status": "managed", **(await real_policy_filter(safe_db))}):
        rows.append(r)
    total = float(sum(Decimal(str(r["premium_amount"])) for r in rows)) if rows else 0.0
    by_product: dict[str, float] = {}
    for r in rows:
        by_product[r["product"]] = by_product.get(r["product"], 0.0) + float(r["premium_amount"])
    meta = {
        "label": LABELS[1], "unit": "AUD",
        "by_product": by_product,
        "managed_count": len(rows),
    }
    if not rows:
        meta["not_yet_measurable"] = True
        meta["reason"] = "no managed policies yet"
    return KpiValue(1, total, meta, None, [total] * 12)


# ---- KPI 2: Income per client ----------------------------------------
async def kpi_2_income_per_client(safe_db, tenant_id: str) -> KpiValue:
    rows = []
    async for r in safe_db.policies.find({"status": "managed", **(await real_policy_filter(safe_db))}):
        rows.append(r)
    commission_rate = 0.20
    commission = sum(float(r["premium_amount"]) for r in rows) * commission_rate
    client_count = len({r["client_id"] for r in rows}) or 0
    scalar = commission / client_count if client_count else 0.0
    meta = {
        "label": LABELS[2], "unit": "AUD",
        "commission_total": commission,
        "commission_rate": commission_rate,
        "fees_total": 0.0,
        "active_client_count": client_count,
        "mocked_commission_rate": True,
    }
    if client_count == 0:
        meta["not_yet_measurable"] = True
        meta["reason"] = "no active clients yet"
    return KpiValue(2, scalar, meta, None, [scalar] * 12)


# ---- KPI 3: Retention rate -------------------------------------------
async def kpi_3_retention_rate(safe_db, tenant_id: str) -> KpiValue:
    rows = await _all_events(safe_db, ["PolicyRenewed", "PolicyLapsed"])
    meta = {
        "label": LABELS[3], "unit": "%",
        "renewed_count": 0, "renewed_value": 0.0,
        "not_yet_measurable": True,
        "reason": "no renewal events yet — pending renewal ingestion",
    }
    for r in rows:
        p = _parse_payload(r["payload"])
        if p and r:
            if "renewed" in (p.get("kind") or ""):
                meta["renewed_count"] += 1
                meta["renewed_value"] += float(p.get("value") or 0)
    if meta["renewed_count"]:
        meta.pop("not_yet_measurable", None)
        meta.pop("reason", None)
    return KpiValue(3, 0.0, meta, None, [0.0] * 12)


# ---- KPI 4: Pipeline velocity ----------------------------------------
async def kpi_4_pipeline_velocity(safe_db, tenant_id: str) -> KpiValue:
    meta = {
        "label": LABELS[4], "unit": "days",
        "stages": {}, "conversion": {},
        "not_yet_measurable": True,
        "reason": "no pipeline events yet — pending pipeline ingestion",
    }
    return KpiValue(4, 0.0, meta, None, [0.0] * 12)


# ---- KPI 5: Response time --------------------------------------------
async def kpi_5_response_time(safe_db, tenant_id: str) -> KpiValue:
    rows = []
    async for r in safe_db.proj_queue.find({"resolved_at": {"$ne": None}}):
        rows.append(r)
    per_class: dict[str, list[float]] = {}
    all_secs: list[float] = []
    events_slice: list[dict[str, Any]] = []
    for r in rows:
        resolved = r["resolved_at"]
        hitl = r["hitl_since"]
        if isinstance(resolved, str):
            resolved = datetime.fromisoformat(resolved)
        if isinstance(hitl, str):
            hitl = datetime.fromisoformat(hitl)
        secs = (resolved - hitl).total_seconds()
        per_class.setdefault(r["class_key"], []).append(secs)
        all_secs.append(secs)
        events_slice.append({
            "envelope_id": str(r["envelope_id"]),
            "class_key": r["class_key"],
            "response_seconds": secs,
        })
    median_all = _median(all_secs)
    meta: dict[str, Any] = {
        "label": LABELS[5], "unit": "seconds", "drillable": True,
        "median_by_class": {k: _median(v) for k, v in per_class.items()},
        "sample_size": len(all_secs),
        "events": events_slice,
    }
    if not all_secs:
        meta["not_yet_measurable"] = True
        meta["reason"] = "no resolved queue cards yet"
    return KpiValue(5, median_all, meta, None, [median_all] * 12)


# ---- KPI 6: Time to quote -------------------------------------------
async def kpi_6_time_to_quote(safe_db, tenant_id: str) -> KpiValue:
    meta = {
        "label": LABELS[6], "unit": "hours",
        "median_hours": 0.0, "sample_size": 0,
        "not_yet_measurable": True,
        "reason": "no endorsement→quote pairs yet — pending endorsement telemetry",
    }
    return KpiValue(6, 0.0, meta, None, [0.0] * 12)


# ---- KPI 7: Claims timeframe -----------------------------------------
async def kpi_7_claims_timeframe(safe_db, tenant_id: str) -> KpiValue:
    meta = {
        "label": LABELS[7], "unit": "%",
        "met": 0, "missed": 0,
        "not_yet_measurable": True,
        "reason": "no claim events yet — pending claim ingestion",
    }
    return KpiValue(7, 0.0, meta, None, [0.0] * 12)


# ---- KPI 8: Autonomous % --------------------------------------------
async def kpi_8_autonomous_pct(safe_db, tenant_id: str) -> KpiValue:
    rows = []
    async for r in safe_db.proj_class_unlock.find({}):
        rows.append(r)
    per_class: dict[str, dict[str, Any]] = {}
    total_seen = 0
    total_agree = 0
    for r in rows:
        seen = int(r.get("decisions_seen", 0))
        agree = int(r.get("agreements", 0))
        pct = (agree / seen * 100.0) if seen else 0.0
        per_class[r["class_key"]] = {
            "decisions_seen": seen, "agreements": agree,
            "agreement_pct": pct,
            "eno_misses": int(r.get("eno_misses", 0)),
            "unlocked": bool(r.get("unlocked", False)),
        }
        total_seen += seen
        total_agree += agree
    overall_pct = (total_agree / total_seen * 100.0) if total_seen else 0.0
    meta: dict[str, Any] = {
        "label": LABELS[8], "unit": "%", "drillable": True,
        "per_class": per_class,
        "total_decisions": total_seen,
        "total_agreements": total_agree,
    }
    if total_seen == 0:
        meta["not_yet_measurable"] = True
        meta["reason"] = "no unlock-tracked decisions yet"
    return KpiValue(8, overall_pct, meta, None, [overall_pct] * 12)


# ---- KPI 9: Cost per matter ------------------------------------------
async def kpi_9_cost_per_matter(safe_db, tenant_id: str) -> KpiValue:
    row = await safe_db.proj_sprint_usage.find_one({})
    tokens = int(row.get("tokens_used", 0)) if row else 0
    invocations = int(row.get("invocations", 0)) if row else 0
    matter_count = await safe_db.proj_matters.count_documents({}) or 0
    cost = tokens / matter_count if matter_count else 0.0
    meta: dict[str, Any] = {
        "label": LABELS[9], "unit": "tokens/matter",
        "tokens_used": tokens, "invocations": invocations,
        "matter_count": matter_count,
        "compute_seconds": 0.0, "human_minutes": 0.0,
    }
    if matter_count == 0:
        meta["not_yet_measurable"] = True
        meta["reason"] = "no matters yet"
    return KpiValue(9, cost, meta, None, [cost] * 12)


# ---- KPI 10: Approvals load ------------------------------------------
async def kpi_10_approvals_load(safe_db, tenant_id: str) -> KpiValue:
    rows = await _all_events(safe_db, ["Approval"])
    per_user: dict[str, int] = {}
    by_day: dict[str, int] = {}
    approvals_slice: list[dict[str, Any]] = []
    for r in rows:
        p = _parse_payload(r["payload"])
        uid = p.get("user_id") or "unknown"
        per_user[uid] = per_user.get(uid, 0) + 1
        ts = r["ts"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        day = ts.date().isoformat()
        by_day[day] = by_day.get(day, 0) + 1
        approvals_slice.append({
            "ts": ts.isoformat(timespec="microseconds") if isinstance(ts, datetime) else str(ts),
            "user_id": uid, "role": p.get("role"),
            "envelope_id": p.get("envelope_id"),
            "decision": p.get("decision"),
        })
    total = sum(per_user.values())
    users = max(len(per_user), 1)
    scalar = total / users
    meta: dict[str, Any] = {
        "label": LABELS[10], "unit": "approvals/person",
        "drillable": True,
        "per_user_count": per_user, "by_day": by_day,
        "sample_size": total, "events": approvals_slice,
    }
    if total == 0:
        meta["not_yet_measurable"] = True
        meta["reason"] = "no Approval events yet"
        scalar = 0.0
    return KpiValue(10, scalar, meta, None, [scalar] * 12)


# ---- KPI 11: Compliance health ---------------------------------------
async def kpi_11_compliance_health(safe_db, tenant_id: str) -> KpiValue:
    open_blocks = await safe_db.proj_active_blocks.count_documents(
        {"cleared_at": None}
    )
    events = await _all_events(safe_db, [
        "ComplianceBlockAdded", "ComplianceBlockCleared",
        "ComplaintOpened", "ComplaintResolved",
    ])
    complaints_open = 0
    complaints_within = 0
    events_slice = []
    for r in events:
        ts = r["ts"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        events_slice.append({
            "ts": ts.isoformat(timespec="microseconds") if isinstance(ts, datetime) else str(ts),
            "type": r["type"],
            "payload": _parse_payload(r["payload"]),
        })
        if r["type"] == "ComplaintOpened":
            complaints_open += 1
        if r["type"] == "ComplaintResolved":
            complaints_within += 1
    total = complaints_open + complaints_within
    within_pct = (complaints_within / total * 100.0) if total else 0.0
    pass_rate = max(0.0, 100.0 - min(open_blocks * 10, 100))
    scalar = pass_rate
    meta: dict[str, Any] = {
        "label": LABELS[11], "unit": "%",
        "drillable": True,
        "open_blocks": open_blocks,
        "complaints_open": complaints_open,
        "complaints_within_timeframe_pct": within_pct,
        "breaches": 0,
        "events": events_slice,
    }
    if open_blocks == 0 and total == 0:
        meta["reason"] = "no compliance events yet; treating as clean"
    return KpiValue(11, scalar, meta, None, [scalar] * 12)


# ---- KPI 12: Cash ----------------------------------------------------
async def kpi_12_cash(safe_db, tenant_id: str) -> KpiValue:
    meta = {
        "label": LABELS[12], "unit": "AUD",
        "trust_exceptions": 0, "receivables_over_30": 0.0,
        "unremitted_premium": 0.0,
        "not_yet_measurable": True,
        "reason": "finance module out of scope for this build",
    }
    return KpiValue(12, 0.0, meta, None, [0.0] * 12)


COMPUTATIONS = {
    1: kpi_1_book, 2: kpi_2_income_per_client,
    3: kpi_3_retention_rate, 4: kpi_4_pipeline_velocity,
    5: kpi_5_response_time, 6: kpi_6_time_to_quote,
    7: kpi_7_claims_timeframe, 8: kpi_8_autonomous_pct,
    9: kpi_9_cost_per_matter, 10: kpi_10_approvals_load,
    11: kpi_11_compliance_health, 12: kpi_12_cash,
}


async def compute_all(store, tenant_id: str) -> list[KpiValue]:
    safe_db = wrap(store)
    values: list[KpiValue] = []
    async with use_tenant(tenant_id):
        for kpi_id in range(1, 13):
            fn = COMPUTATIONS[kpi_id]
            values.append(await fn(safe_db, tenant_id))
    values = await _attach_history(safe_db, tenant_id, values)
    return values


async def persist_daily(
    store, tenant_id: str, values: list[KpiValue],
    day: "date | None" = None,
) -> None:
    """R5c-3: Mongo mirror of `kpis.persist_daily`.

    Upserts one document per `(tenant_id, day, kpi_id)` into the
    tenant-scoped `proj_kpi_daily` collection. `day` is stored as an
    ISO date string to match `projections_mongo.upsert_kpi_daily`
    (which already handles the same collection under
    `WorkflowSucceeded` etc.).
    """
    from datetime import date as _date, datetime as _dt, timezone as _tz
    from app.kernel.tenant_ctx import use_tenant
    day = day or _dt.now(_tz.utc).date()
    day_iso = day.isoformat() if isinstance(day, _date) else str(day)
    safe_db = wrap(store)
    async with use_tenant(tenant_id):
        for v in values:
            await safe_db.proj_kpi_daily.update_one(
                {"day": day_iso, "kpi_id": int(v.kpi_id)},
                {"$set": {
                    "value_scalar": float(v.scalar),
                    "value_meta": v.meta or {},
                    "delta_prev": (
                        None if v.delta_prev is None
                        else float(v.delta_prev)
                    ),
                    "spark_series": [float(x) for x in v.spark_series],
                    "computed_at": _dt.now(_tz.utc),
                }},
                upsert=True,
            )


async def _attach_history(
    safe_db, tenant_id: str, values: list[KpiValue]
) -> list[KpiValue]:
    async with use_tenant(tenant_id):
        history = []
        async for r in safe_db.proj_kpi_daily.find({}).sort([
            ("kpi_id", 1), ("day", -1),
        ]):
            history.append(r)
    by_id: dict[int, list[tuple[str, float]]] = {}
    for r in history:
        by_id.setdefault(int(r["kpi_id"]), []).append(
            (r["day"], float(r["value_scalar"]))
        )
    out: list[KpiValue] = []
    for v in values:
        hist = by_id.get(v.kpi_id, [])
        delta = None
        if hist:
            prev = hist[0][1]
            delta = float(v.scalar) - prev
        spark = [float(h[1]) for h in hist[:11][::-1]] + [float(v.scalar)]
        spark = spark[-12:] or [float(v.scalar)] * 12
        out.append(KpiValue(
            kpi_id=v.kpi_id, scalar=v.scalar, meta=v.meta,
            delta_prev=delta, spark_series=spark,
        ))
    return out
