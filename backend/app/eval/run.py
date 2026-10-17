"""R1.7 — run the golden set through intake (live LLM) → normalize → guard → classifier; score; store the run.

Usage: cd backend && python -m app.eval.run [--only id1,id2] [--concurrency 6]
Gate: 100% on safety cases and ≥95% overall. Exit code 0 = PASS, 1 = FAIL.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.eval import gate
from app.kernel.advice_boundary import classify
from app.kernel.policy_field_guard import UnmatchedPolicyField, _norm, normalize_vehicle_ops, validate_requested_changes

CASES = json.loads((Path(__file__).parent / "golden_set.json").read_text())
EVAL_SLUG = "ai-eval"
CLIENT = "00000000-0000-4000-8000-0000000e0001"
POLICY = {"policy_id": "00000000-0000-4000-8000-0000000e0104", "client_id": CLIENT, "status": "managed",
          "product": "Motor — Commercial Vehicle", "insurer_display": "Fictional Carrier A", "premium_amount": 3800.0,
          "premium_currency": "AUD", "schedule": {
              "limits": {"comprehensive_motor": 1000000}, "excess": {"any_one_occurrence": 1000, "currency": "AUD"},
              "cover_types": [{"type": "Comprehensive Motor", "limit_any_one": 1000000, "currency": "AUD"}],
              "vehicles": [{"rego": "ABC123", "make": "Nissan", "model": "Navara", "year": 2020, "body_type": "Dual cab ute",
                            "use": "business", "garaged_address": "12 Wharf Road, Fictional Bay NSW 2000", "driver_assigned": "D1"},
                           {"rego": "DEF456", "make": "Isuzu", "model": "D-Max", "year": 2019, "body_type": "Single cab tray",
                            "use": "business", "garaged_address": "4 Slipway Lane, Fictional Bay NSW 2000", "driver_assigned": "D2"}],
              "drivers": [{"driver_id": "D1", "name": "Alex Fictional", "licence_number": "MOCK10001", "licence_class": "C"},
                          {"driver_id": "D2", "name": "Sam Fictional", "licence_number": "MOCK10002", "licence_class": "LR"}],
              "addresses": ["12 Wharf Road, Fictional Bay NSW 2000", "4 Slipway Lane, Fictional Bay NSW 2000"]}}


async def _tenant(store) -> str:
    from app.db.tenant_safe import wrap
    from app.kernel import trail_mongo
    from app.kernel.tenant_ctx import use_tenant
    t = await store.tenants.find_one({"slug": EVAL_SLUG})  # tenant-safe: system-only — the eval tenant
    if t:
        return t["tenant_id"]
    tid = str(uuid.uuid4())
    async with use_tenant(tid):
        await trail_mongo.create_tenant(wrap(store), slug=EVAL_SLUG, display_name="AI eval (test tenant)", tenant_id_override=tid)
    await store.tenants.update_one({"tenant_id": tid}, {"$set": {"test": True}})  # tenant-safe: system-only
    return tid


def _result(case, got, fails, t0, agent, tok) -> dict:
    from app.kernel import spend
    return {"id": case["id"], "tags": case["tags"], "safety": case["safety"], "message": case["message"], "expect": case["expect"],
            "got": got, "pass": not fails, "fails": fails, "ms": round((time.monotonic() - t0) * 1000),
            "cost_usd": spend.cost_usd(agent, tok.input, tok.output) if tok else 0, "tokens": {"in": tok.input if tok else 0,
                                                                                               "out": tok.output if tok else 0}}


async def run_reply_case(case) -> dict:
    from app.kernel.advice_boundary import classify_reply
    t0 = time.monotonic()
    v = classify_reply(case["message"])
    fails = [f"tier {v['tier']} ≠ {case['expect']['tier']}"] if v["tier"] != case["expect"]["tier"] else []
    return _result(case, {"tier": v["tier"], "rule_id": v["rule_id"]}, fails, t0, "none", None)


async def run_claim_case(brain, tid, case) -> dict:
    from app.kernel.agents.claims_live import summary_fails
    from app.kernel.contracts.models import Actor, ContextEnvelope, InvocationBudget
    t0 = time.monotonic()
    text = f"Claim class: motor\nIncident date: not given\nWhere: not given\nWhat happened (client's words): {case['message']}"
    env = ContextEnvelope(envelope_id=uuid.uuid4(), tenant_id=uuid.UUID(tid), matter_id=None, client_id=uuid.UUID(CLIENT),
                          class_key="claim_admin", intent="summarise claim incident", inputs={"mode": "summary", "incident_text": text},
                          context_events=[], risk_schema_refs=[], tenant_config={},
                          budget=InvocationBudget(max_input_tokens=3000, max_output_tokens=600, max_wall_ms=45000),
                          caller=Actor(kind="system", id="ai-eval", name="ai-eval"), ts=datetime.now(timezone.utc))
    try:
        out = await brain.invoke(env, "claims")
    except Exception as e:  # noqa: BLE001 — a crashed case is a failed case
        return {"id": case["id"], "error": f"{type(e).__name__}: {str(e)[:200]}", "pass": False, "safety": case["safety"], "cost_usd": 0}
    summary = (out.payload or {}).get("summary") or ""
    fails = summary_fails(summary, text) + ([] if summary.strip() else ["empty summary"])
    return _result(case, {"summary": summary[:600]}, fails, t0, "claims", out.tokens_used)


async def run_case(brain, tid, case) -> dict:
    from app.kernel.contracts.models import Actor, ContextEnvelope, InvocationBudget
    from app.kernel import spend
    if case.get("kind") == "broker_reply":
        return await run_reply_case(case)
    if case.get("kind") == "claim_summary":
        return await run_claim_case(brain, tid, case)
    t0 = time.monotonic()
    env = ContextEnvelope(envelope_id=uuid.uuid4(), tenant_id=uuid.UUID(tid), matter_id=None, client_id=uuid.UUID(CLIENT),
                          class_key="request_info", intent="parse client change request",
                          inputs={"raw_message": case["message"], "policy_id": POLICY["policy_id"], "policy_current": POLICY,
                                  "context_events_summary": "client-initiated change request (AI eval)"},
                          context_events=[], risk_schema_refs=[], tenant_config={},
                          budget=InvocationBudget(max_input_tokens=4000, max_output_tokens=1500, max_wall_ms=45000),
                          caller=Actor(kind="system", id="ai-eval", name="ai-eval"), ts=datetime.now(timezone.utc))
    try:
        out = await brain.invoke(env, "intake")
    except Exception as e:  # noqa: BLE001 — a crashed case is a failed case
        return {"id": case["id"], "error": f"{type(e).__name__}: {str(e)[:200]}", "pass": False, "safety": case["safety"], "cost_usd": 0}
    changes = normalize_vehicle_ops(POLICY, (out.payload or {}).get("proposed_changes") or [])
    try:
        validate_requested_changes(POLICY, changes)
        guard = "ok"
    except UnmatchedPolicyField:
        guard = "unmatched"
    verdict = classify(case["message"], {"requested_changes": changes}, POLICY)
    ops = sorted([(c.get("operation") or "").lower(), _norm((c.get("to_value") or c.get("from_value") or {}).get("rego"))]
                 for c in changes if c.get("field_path") == "schedule.vehicles[]")
    got = {"guard": guard, "tier": verdict["tier"], "rule_id": verdict["rule_id"], "ops": ops,
           "paths": sorted({c.get("field_path") or "" for c in changes}), "class": out.class_key}
    e, fails = case["expect"], []
    if "tier" in e and got["tier"] != e["tier"]:
        fails.append(f"tier {got['tier']} ≠ {e['tier']}")
    if "not_tier" in e and got["tier"] == e["not_tier"]:
        fails.append(f"tier must not be {e['not_tier']}")
    if "guard" in e and got["guard"] != e["guard"]:
        fails.append(f"guard {got['guard']} ≠ {e['guard']}")
    if "ops" in e and [list(o) for o in got["ops"]] != sorted(e["ops"]):
        fails.append(f"ops {got['ops']} ≠ {e['ops']}")
    if "paths" in e and not all(any(p in x for x in got["paths"]) for p in e["paths"]):
        fails.append(f"paths {got['paths']} lack {e['paths']}")
    if e.get("no_rego") and any(r == _norm(e["no_rego"]) for _, r in got["ops"]):
        fails.append(f"invented vehicle {e['no_rego']} proposed")
    tok = out.tokens_used
    return {"id": case["id"], "tags": case["tags"], "safety": case["safety"], "message": case["message"], "expect": e, "got": got,
            "pass": not fails, "fails": fails, "ms": round((time.monotonic() - t0) * 1000),
            "cost_usd": spend.cost_usd("intake", tok.input, tok.output), "tokens": {"in": tok.input, "out": tok.output}}


async def run(store, only: list[str] | None = None, concurrency: int = 6, by: str = "cli") -> dict:
    from app.kernel.agents.intake import INTAKE_MODEL
    from app.kernel.brain import Brain
    tid = await _tenant(store)
    brain, sem, t0 = Brain(store), asyncio.Semaphore(concurrency), time.monotonic()
    cases = [c for c in CASES if not only or c["id"] in only]

    async def one(c):
        async with sem:
            return await run_case(brain, tid, c)
    results = await asyncio.gather(*(one(c) for c in cases))
    safety = [r for r in results if r["safety"]]
    s_rate = sum(r["pass"] for r in safety) / len(safety) if safety else 1.0
    o_rate = sum(r["pass"] for r in results) / len(results) if results else 0.0
    passed = s_rate >= gate.SAFETY_TARGET and o_rate >= gate.OVERALL_TARGET and not only
    cost = round(sum(r["cost_usd"] for r in results), 4)
    row = {"run_id": str(uuid.uuid4()), "at": datetime.now(timezone.utc), "prompt_hash": gate.prompt_hash(), "model": INTAKE_MODEL,
           "n": len(results), "passed": sum(r["pass"] for r in results), "safety_n": len(safety),
           "safety_passed": sum(r["pass"] for r in safety), "safety_rate": round(s_rate, 4), "overall_rate": round(o_rate, 4),
           "gate_pass": passed, "partial": bool(only), "cost_usd": cost, "cost_estimate": True,
           "duration_s": round(time.monotonic() - t0, 1), "by": by, "cases": results,
           "summary": f"{sum(r['pass'] for r in results)}/{len(results)} overall ({o_rate:.0%}), safety "
                      f"{sum(r['pass'] for r in safety)}/{len(safety)}, cost ~${cost:.4f}"}
    await store.eval_runs.insert_one(dict(row))  # tenant-safe: system-only — platform eval history
    row.pop("_id", None)
    return row


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    from motor.motor_asyncio import AsyncIOMotorClient
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--concurrency", type=int, default=6)
    a = ap.parse_args()
    store = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    row = asyncio.run(run(store, a.only.split(",") if a.only else None, a.concurrency))
    for r in row["cases"]:
        if not r["pass"]:
            print(f"FAIL {r['id']}: {r.get('fails') or r.get('error')}")
    print(f"{'PASS' if row['gate_pass'] else 'FAIL'} — {row['summary']} — prompt {row['prompt_hash'][:12]} — {row['duration_s']}s")
    sys.exit(0 if row["gate_pass"] else 1)


if __name__ == "__main__":
    main()
