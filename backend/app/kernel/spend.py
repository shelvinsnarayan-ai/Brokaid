"""R0.5 — dollar-aware spend ESTIMATE from advertised per-model rates.

Not provider billing: tokens × configured USD/MTok. Rates overridable via
SPEND_RATES_JSON='{"model": {"input": 3.0, "output": 15.0}}'.
SPEND_CAP_USD = hard cap (Brain refuses new calls once reached).
SPEND_ALERT_USD_50 / _80 / _100 = soft thresholds in USD (default: cap × pct).
"""
from __future__ import annotations

import json
import logging
import os

from app.db.tenant_safe import TenantSafeDatabase, wrap
from app.kernel.tenant_ctx import use_tenant

log = logging.getLogger("brokaid.spend")

DEFAULT_RATES = {"claude-sonnet-4-6": {"input": 3.0, "output": 15.0}}
LIVE_MODEL = "claude-sonnet-4-6"
AGENT_MODEL = {a: LIVE_MODEL for a in ("intake", "placement", "compliance", "liaison", "claims")}


def rates() -> dict:
    raw = os.environ.get("SPEND_RATES_JSON", "").strip()
    return {**DEFAULT_RATES, **(json.loads(raw) if raw else {})}


def cost_usd(agent_name: str, tokens_in: int, tokens_out: int) -> float:
    r = rates().get(AGENT_MODEL.get(agent_name, ""))
    if not r:
        return 0.0
    return round((tokens_in * r["input"] + tokens_out * r["output"]) / 1_000_000, 6)


def _f(name: str) -> float | None:
    v = os.environ.get(name, "").strip()
    return float(v) if v else None


def cap_usd() -> float | None:
    return _f("SPEND_CAP_USD")


def thresholds() -> list[tuple[int, float]]:
    cap = cap_usd()
    out = []
    for pct in (50, 80, 100):
        v = _f(f"SPEND_ALERT_USD_{pct}")
        if v is None and cap is not None:
            v = cap * pct / 100
        if v is not None:
            out.append((pct, v))
    return out


async def summary(store, tenant_id: str) -> dict:
    safe = store if isinstance(store, TenantSafeDatabase) else wrap(store)
    async with use_tenant(tenant_id):
        usage = await safe.proj_sprint_usage.find_one({}) or {}
        by_agent = [r async for r in safe.proj_sprint_usage_by_agent.find({})]
    agents, cost = [], 0.0
    for r in by_agent:
        c = cost_usd(r["agent_name"], int(r.get("tokens_used_input", 0)), int(r.get("tokens_used_output", 0)))
        cost += c
        agents.append({"agent_name": r["agent_name"], "model": AGENT_MODEL.get(r["agent_name"], "mock"),
                       "tokens_input": int(r.get("tokens_used_input", 0)),
                       "tokens_output": int(r.get("tokens_used_output", 0)),
                       "invocations": int(r.get("invocations", 0)), "cost_usd": round(c, 4)})
    cap = cap_usd()
    return {
        "tokens_used": int(usage.get("tokens_used", 0)),
        "invocations": int(usage.get("invocations", 0)),
        "cost_usd": round(cost, 4),
        "cost_usd_tracked": round(float(usage.get("cost_usd_total", 0.0)), 4),
        "cap_usd": cap,
        "pct_of_cap": round(cost / cap * 100, 1) if cap else None,
        "thresholds_usd": {str(p): v for p, v in thresholds()},
        "alerts_fired": sorted(usage.get("spend_alerts_fired") or []),
        "rates_usd_per_mtok": rates(),
        "by_agent": agents,
        "estimate": True,
    }


async def record_alerts(store, tenant_id: str, cost: float, emit) -> list[int]:
    """WARN + SpendAlert event once per threshold crossed. Returns newly fired pcts."""
    safe = store if isinstance(store, TenantSafeDatabase) else wrap(store)
    async with use_tenant(tenant_id):
        usage = await safe.proj_sprint_usage.find_one({}) or {}
    fired = set(usage.get("spend_alerts_fired") or [])
    new = [p for p, v in thresholds() if cost >= v and p not in fired]
    for p in new:
        log.warning("spend alert: %s%% threshold crossed (estimate $%.4f, tenant %s)", p, cost, tenant_id)
        await emit(tenant_id, type="SpendAlert", actor={"kind": "system", "id": "brain", "name": "brain"},
                   payload={"threshold_pct": p, "cost_usd": round(cost, 4), "cap_usd": cap_usd(),
                            "estimate": True},
                   idempotency_key=f"spend:alert:{tenant_id}:{p}")
        async with use_tenant(tenant_id):
            await safe.proj_sprint_usage.update_one({}, {"$addToSet": {"spend_alerts_fired": p}}, upsert=True)
    return new
