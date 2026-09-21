"""Twelve deterministic KPI computations from ledger + policies.

Each returns:
    {
      "scalar": <NUMERIC>,
      "meta": {..., "not_yet_measurable"?: bool, "reason"?: str,
               "mocked_commission_rate"?: bool, ...},
      "delta_prev": <NUMERIC | None>,
      "spark_series": [<NUMERIC>, ... up to 12],
    }

No LLM, no model calls, no random. All numbers come from the events
table + policies + proj_class_unlock. Where inputs don't exist yet,
KPIs return a shaped 0/null with `meta.not_yet_measurable=true` and a
human-readable reason.

The four *drillable* KPIs (5 response time, 8 autonomy%,
10 approvals load, 11 compliance health) declare `meta.drillable=true`
and expose the raw event slice used to compute them.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any



@dataclass(frozen=True)
class KpiValue:
    kpi_id: int
    scalar: float
    meta: dict[str, Any]
    delta_prev: float | None
    spark_series: list[float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kpi_id": self.kpi_id,
            "scalar": float(self.scalar),
            "meta": self.meta,
            "delta_prev": None if self.delta_prev is None else float(self.delta_prev),
            "spark_series": [float(x) for x in self.spark_series],
        }


LABELS = {
    1:  "Book",
    2:  "Income per client",
    3:  "Retention rate",
    4:  "Pipeline velocity",
    5:  "Response time",
    6:  "Time to quote",
    7:  "Claims timeframe compliance",
    8:  "Autonomous %",
    9:  "Cost per matter",
    10: "Approvals load",
    11: "Compliance health",
    12: "Cash",
}
DRILLABLE = {5, 8, 10, 11}


def _parse_payload(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


async def _events(
    conn: object, tenant_id: str,
    types: tuple[str, ...] | None = None,
) -> list[dict]:
    q = ("SELECT event_id, ts, type, payload, actor "
         "  FROM events "
         " WHERE tenant_id = $1::uuid ")
    args: list[Any] = [tenant_id]
    if types:
        q += " AND type = ANY($2::text[]) "
        args.append(list(types))
    q += " ORDER BY seq ASC"
    return await conn.fetch(q, *args)


# --- individual KPI computations ---------------------------------------


async def kpi_1_book(conn, tenant_id: str) -> KpiValue:
    rows = await conn.fetch(
        "SELECT product, premium_amount FROM policies "
        "WHERE tenant_id = $1::uuid AND status = 'managed'", tenant_id,
    )
    total = float(sum(Decimal(r["premium_amount"]) for r in rows)) if rows else 0.0
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


async def kpi_2_income_per_client(conn, tenant_id: str) -> KpiValue:
    rows = await conn.fetch(
        "SELECT client_id, premium_amount FROM policies "
        "WHERE tenant_id = $1::uuid AND status = 'managed'", tenant_id,
    )
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


async def kpi_3_retention_rate(conn, tenant_id: str) -> KpiValue:
    rows = await conn.fetch(
        "SELECT payload FROM events "
        "WHERE tenant_id = $1::uuid AND type = ANY($2::text[])",
        tenant_id, ["PolicyRenewed", "PolicyLapsed"],
    )
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


async def kpi_4_pipeline_velocity(conn, tenant_id: str) -> KpiValue:
    meta = {
        "label": LABELS[4], "unit": "days",
        "stages": {}, "conversion": {},
        "not_yet_measurable": True,
        "reason": "no pipeline events yet — pending pipeline ingestion",
    }
    return KpiValue(4, 0.0, meta, None, [0.0] * 12)


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    m = n // 2
    return (s[m] if n % 2 else (s[m - 1] + s[m]) / 2)


async def kpi_5_response_time(conn, tenant_id: str) -> KpiValue:
    """Inbound acknowledgement → resolution, by class.

    Uses proj_queue.hitl_since → resolved_at for resolved rows.
    Drillable: exposes per-envelope pairs.
    """
    rows = await conn.fetch(
        """
        SELECT envelope_id, class_key, hitl_since, resolved_at
          FROM proj_queue
         WHERE tenant_id = $1::uuid AND resolved_at IS NOT NULL
        """,
        tenant_id,
    )
    per_class: dict[str, list[float]] = {}
    all_secs: list[float] = []
    events_slice: list[dict[str, Any]] = []
    for r in rows:
        secs = (r["resolved_at"] - r["hitl_since"]).total_seconds()
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


async def kpi_6_time_to_quote(conn, tenant_id: str) -> KpiValue:
    # KPI-6 is not-yet-measurable in this build (no endorsement->quote
    # pairs land until phase 8). Return the placeholder without issuing
    # the events fetch — the result was unused (F841 canary) and the
    # round-trip was pure waste.
    meta = {
        "label": LABELS[6], "unit": "hours",
        "median_hours": 0.0, "sample_size": 0,
        "not_yet_measurable": True,
        "reason": "no endorsement→quote pairs yet — pending endorsement telemetry",
    }
    return KpiValue(6, 0.0, meta, None, [0.0] * 12)


async def kpi_7_claims_timeframe(conn, tenant_id: str) -> KpiValue:
    meta = {
        "label": LABELS[7], "unit": "%",
        "met": 0, "missed": 0,
        "not_yet_measurable": True,
        "reason": "no claim events yet — pending claim ingestion",
    }
    return KpiValue(7, 0.0, meta, None, [0.0] * 12)


async def kpi_8_autonomous_pct(conn, tenant_id: str) -> KpiValue:
    """Autonomous % + agreement rate per class from proj_class_unlock."""
    rows = await conn.fetch(
        """
        SELECT class_key, decisions_seen, agreements, eno_misses, unlocked
          FROM proj_class_unlock
         WHERE tenant_id = $1::uuid
        """,
        tenant_id,
    )
    per_class: dict[str, dict[str, Any]] = {}
    total_seen = 0
    total_agree = 0
    for r in rows:
        seen = int(r["decisions_seen"])
        agree = int(r["agreements"])
        pct = (agree / seen * 100.0) if seen else 0.0
        per_class[r["class_key"]] = {
            "decisions_seen": seen, "agreements": agree,
            "agreement_pct": pct,
            "eno_misses": int(r["eno_misses"]),
            "unlocked": bool(r["unlocked"]),
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


async def kpi_9_cost_per_matter(conn, tenant_id: str) -> KpiValue:
    row = await conn.fetchrow(
        "SELECT tokens_used, invocations FROM proj_sprint_usage "
        "WHERE tenant_id = $1::uuid", tenant_id,
    )
    tokens = int(row["tokens_used"]) if row else 0
    invocations = int(row["invocations"]) if row else 0
    matter_count = await conn.fetchval(
        "SELECT COUNT(*) FROM proj_matters WHERE tenant_id = $1::uuid",
        tenant_id,
    ) or 0
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


async def kpi_10_approvals_load(conn, tenant_id: str) -> KpiValue:
    """Approvals per person per day + median approval time."""
    rows = await conn.fetch(
        "SELECT ts, payload FROM events "
        "WHERE tenant_id = $1::uuid AND type = 'Approval' ORDER BY seq ASC",
        tenant_id,
    )
    per_user: dict[str, int] = {}
    by_day: dict[str, int] = {}
    approvals_slice: list[dict[str, Any]] = []
    for r in rows:
        p = _parse_payload(r["payload"])
        uid = p.get("user_id") or "unknown"
        per_user[uid] = per_user.get(uid, 0) + 1
        day = r["ts"].date().isoformat()
        by_day[day] = by_day.get(day, 0) + 1
        approvals_slice.append({
            "ts": r["ts"].isoformat(timespec="microseconds"),
            "user_id": uid, "role": p.get("role"),
            "envelope_id": p.get("envelope_id"),
            "decision": p.get("decision"),
        })
    total = sum(per_user.values())
    users = max(len(per_user), 1)
    scalar = total / users  # approvals per person
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


async def kpi_11_compliance_health(conn, tenant_id: str) -> KpiValue:
    open_blocks_row = await conn.fetchrow(
        "SELECT COUNT(*) AS n FROM proj_active_blocks "
        "WHERE tenant_id = $1::uuid AND cleared_at IS NULL",
        tenant_id,
    )
    open_blocks = int(open_blocks_row["n"]) if open_blocks_row else 0
    events = await conn.fetch(
        "SELECT ts, type, payload FROM events "
        "WHERE tenant_id = $1::uuid AND type = ANY($2::text[]) "
        "ORDER BY seq ASC",
        tenant_id,
        ["ComplianceBlockAdded", "ComplianceBlockCleared",
         "ComplaintOpened", "ComplaintResolved"],
    )
    complaints_open = 0
    complaints_within = 0
    events_slice = []
    for r in events:
        events_slice.append({
            "ts": r["ts"].isoformat(timespec="microseconds"),
            "type": r["type"],
            "payload": _parse_payload(r["payload"]),
        })
        if r["type"] == "ComplaintOpened":
            complaints_open += 1
        if r["type"] == "ComplaintResolved":
            complaints_within += 1
    total = complaints_open + complaints_within
    within_pct = (complaints_within / total * 100.0) if total else 0.0
    # File-audit pass rate proxied at 100 minus block density (bounded).
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
        # Everything zero → pass rate is technically 100, but flag it as
        # trivially measurable so the UI shows the "no complaints yet"
        # sub-line rather than a bare 100.
        meta["reason"] = "no compliance events yet; treating as clean"
    return KpiValue(11, scalar, meta, None, [scalar] * 12)


async def kpi_12_cash(conn, tenant_id: str) -> KpiValue:
    meta = {
        "label": LABELS[12], "unit": "AUD",
        "trust_exceptions": 0, "receivables_over_30": 0.0,
        "unremitted_premium": 0.0,
        "not_yet_measurable": True,
        "reason": "finance module out of scope for this build",
    }
    return KpiValue(12, 0.0, meta, None, [0.0] * 12)


COMPUTATIONS = {
    1: kpi_1_book,      2: kpi_2_income_per_client,
    3: kpi_3_retention_rate, 4: kpi_4_pipeline_velocity,
    5: kpi_5_response_time, 6: kpi_6_time_to_quote,
    7: kpi_7_claims_timeframe, 8: kpi_8_autonomous_pct,
    9: kpi_9_cost_per_matter, 10: kpi_10_approvals_load,
    11: kpi_11_compliance_health, 12: kpi_12_cash,
}


async def compute_all(pool: object, tenant_id: str) -> list[KpiValue]:
    """Run all 12 computations for a tenant. Deterministic."""
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            values: list[KpiValue] = []
            for kpi_id in range(1, 13):
                fn = COMPUTATIONS[kpi_id]
                values.append(await fn(c, tenant_id))
    # attach prev-day deltas + spark from history
    values = await _attach_history(pool, tenant_id, values)
    return values


async def _attach_history(
    pool: object, tenant_id: str, values: list[KpiValue]
) -> list[KpiValue]:
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            history = await c.fetch(
                """
                SELECT kpi_id, day, value_scalar
                  FROM proj_kpi_daily
                 WHERE tenant_id = $1::uuid
                 ORDER BY kpi_id ASC, day DESC
                """,
                tenant_id,
            )
    by_id: dict[int, list[tuple[date, float]]] = {}
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


async def persist_daily(
    pool: object, tenant_id: str, values: list[KpiValue],
    day: date | None = None,
) -> None:
    day = day or datetime.now(timezone.utc).date()
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            for v in values:
                await c.execute(
                    """
                    INSERT INTO proj_kpi_daily
                      (tenant_id, day, kpi_id, value_scalar, value_meta,
                       delta_prev, spark_series, computed_at)
                    VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6, $7, NOW())
                    ON CONFLICT (tenant_id, day, kpi_id) DO UPDATE SET
                      value_scalar = EXCLUDED.value_scalar,
                      value_meta   = EXCLUDED.value_meta,
                      delta_prev   = EXCLUDED.delta_prev,
                      spark_series = EXCLUDED.spark_series,
                      computed_at  = EXCLUDED.computed_at
                    """,
                    tenant_id, day, v.kpi_id,
                    Decimal(str(v.scalar)),
                    json.dumps(v.meta, default=str),
                    None if v.delta_prev is None else Decimal(str(v.delta_prev)),
                    [Decimal(str(x)) for x in v.spark_series],
                )
