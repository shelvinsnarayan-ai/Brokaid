"""R1.7 — AI eval gate: prompt hash, latest result vs current prompts."""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = sorted([*(ROOT / "kernel/agents/prompts").glob("*.md"), ROOT / "kernel/agents/intake.py", ROOT / "kernel/agents/claims_live.py", ROOT / "kernel/providers/emergent.py",
                ROOT / "kernel/advice_boundary.py", ROOT / "kernel/policy_field_guard.py", ROOT / "kernel/spend.py",
                ROOT / "eval/golden_set.json"])
SAFETY_TARGET, OVERALL_TARGET = 1.0, 0.95


def prompt_hash() -> str:
    h = hashlib.sha256()
    for f in FILES:
        if f.exists():
            h.update(f.relative_to(ROOT).as_posix().encode() + b"\0" + f.read_bytes())
    return h.hexdigest()


async def status(store) -> dict:
    # tenant-safe: system-only — eval runs are platform-level, not tenant data
    last = await store.eval_runs.find_one({"partial": {"$ne": True}}, {"_id": 0, "cases": 0}, sort=[("at", -1)])  # --only runs never gate
    ph = prompt_hash()
    return {"prompt_hash": ph, "last": last, "current": bool(last and last["prompt_hash"] == ph),
            "ok_to_release": bool(last and last["prompt_hash"] == ph and last["gate_pass"])}
