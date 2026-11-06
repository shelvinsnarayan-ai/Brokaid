"""R3.11: multi-model UX review against A20 (§2 heuristic matrix, §3 task matrix). The consolidation is deterministic code, not an LLM."""
from __future__ import annotations

import statistics

SCALE = "0–4; 4 = meets standard. Release bar: ≥3.0 on every row, both surfaces."
# A20 §2 rows, in order, with the 12 Oct baseline (efd1841). None = n/a.
ROWS = [
    ("visibility", "Visibility of system status", 1, 1),
    ("real_world", "Match to the real world", 1, 0),
    ("control", "User control and freedom", 2, 2),
    ("consistency", "Consistency and standards", 2, 1),
    ("error_prevention", "Error prevention", 1, 1),
    ("recognition", "Recognition over recall", 1, 1),
    ("flexibility", "Flexibility and efficiency", 2, 3),
    ("aesthetic", "Aesthetic and minimalist design", 3, 1),
    ("recovery", "Error recognition and recovery", 1, 1),
    ("help", "Help and documentation", 1, 1),
    ("am_i_covered", "Answers \"am I covered?\" in one glance", 0, None),
    ("who_by_when", "Shows who holds my matter and by when", 1, 2),
    ("no_engine", "Never exposes the engine", 1, 0),
    ("regulatory", "Regulatory surfaces correct and placed", 1, 1),
]
BASELINE_MEAN = {"client": 1.3, "broker": 1.1}
SURFACES = ("client", "broker")
ARCHETYPES = [("farmer", "Farmer (ute, shed, hay)"), ("landlord", "Landlord (2 units)"), ("tradie", "Tradie (ute, tools, PL)"),
              ("cafe", "Café owner (pack)"), ("household", "Household (2 Owners)"), ("professional", "Professional (PI)")]
JOBS = [("J1", "Is the ute / unit / shed insured, for how much?"), ("J2", "Get a certificate naming a counterparty"),
        ("J3", "Lodge a claim for a specific thing"), ("J4", "See what I owe and how to pay"),
        ("J5", "Confirm renewal details and sums insured"), ("J6", "Let my spouse / bookkeeper act on this"),
        ("J7", "Find last year's schedule / PDS")]
NA_CELLS = {("J1", "professional"), ("J2", "household")}  # A20 §3 marks these n/a
INSUFFICIENT = "insufficient evidence"


def validate(out: dict, shot_ids: set[str]) -> list[str]:
    """Problems with one model's JSON (empty list = valid)."""
    errs = []
    h = out.get("heuristics") if isinstance(out, dict) else None
    if not isinstance(h, dict):
        return ["missing object 'heuristics'"]
    for key, _label, _bc, bb in ROWS:
        for surf in SURFACES:
            cell = (h.get(key) or {}).get(surf)
            if not isinstance(cell, dict):
                errs.append(f"heuristics.{key}.{surf} missing")
                continue
            sc = cell.get("score")
            na_ok = surf == "broker" and bb is None
            if not (isinstance(sc, int) and 0 <= sc <= 4) and sc != INSUFFICIENT and not (na_ok and sc == "n/a"):
                errs.append(f"heuristics.{key}.{surf}.score invalid: {sc!r}")
            ev = cell.get("evidence")
            if isinstance(sc, int) and (not isinstance(ev, list) or not ev):
                errs.append(f"heuristics.{key}.{surf}.evidence must cite screenshot ids")
            bad = [e for e in ev or [] if e not in shot_ids] if isinstance(ev, list) else []
            if bad:
                errs.append(f"heuristics.{key}.{surf}.evidence unknown ids: {bad[:4]}")
    t = out.get("tasks")
    if not isinstance(t, dict):
        return errs + ["missing object 'tasks'"]
    for jid, _ in JOBS:
        for a, _ in ARCHETYPES:
            cell = (t.get(jid) or {}).get(a)
            if not isinstance(cell, dict) or cell.get("success") not in ("Y", "Partial", "N", "n/a"):
                errs.append(f"tasks.{jid}.{a}.success must be Y/Partial/N/n/a")
    return errs


def _num(cell) -> int | None:
    sc = (cell or {}).get("score")
    return sc if isinstance(sc, int) else None


def consolidate(models: dict[str, dict]) -> dict:
    """models: {model_name: validated output}. Returns per-row stats, surface means, task consensus and a ranked fix list."""
    rows, fixes = [], []
    for key, label, bc, bb in ROWS:
        for surf, base in (("client", bc), ("broker", bb)):
            per = {m: _num(o["heuristics"][key][surf]) for m, o in models.items()}
            vals = [v for v in per.values() if v is not None]
            mean = round(statistics.mean(vals), 2) if vals else None
            r = {"row": key, "label": label, "surface": surf, "scores": per, "mean": mean, "min": min(vals) if vals else None,
                 "max": max(vals) if vals else None, "n": len(vals), "baseline": base,
                 "delta": round(mean - base, 2) if mean is not None and base is not None else None}
            r["spread"] = (r["max"] - r["min"]) if vals else None
            r["disagreement"] = bool(vals) and r["spread"] >= 2
            r["pass"] = None if mean is None else mean >= 3.0
            rows.append(r)
            gap = max(0.0, 3.0 - mean) if mean is not None else 0.0
            texts = {m: o["heuristics"][key][surf].get("fix") for m, o in models.items()
                     if _num(o["heuristics"][key][surf]) is not None and _num(o["heuristics"][key][surf]) < 3 and o["heuristics"][key][surf].get("fix")}
            if gap > 0 and texts:
                fixes.append({"row": key, "label": label, "surface": surf, "frequency": len(texts), "gap": round(gap, 2),
                              "priority": round(len(texts) * gap, 2), "fixes": texts})
    fixes.sort(key=lambda f: (-f["priority"], -f["gap"], f["row"], f["surface"]))
    surf_means = {}
    for surf in SURFACES:
        per_model = {}
        for m in models:
            vals = [r["scores"][m] for r in rows if r["surface"] == surf and r["scores"][m] is not None]
            per_model[m] = round(statistics.mean(vals), 2) if vals else None
        cons = [r["mean"] for r in rows if r["surface"] == surf and r["mean"] is not None]
        surf_means[surf] = {"per_model": per_model, "consensus": round(statistics.mean(cons), 2) if cons else None,
                            "baseline": BASELINE_MEAN[surf], "rows_passing": sum(1 for r in rows if r["surface"] == surf and r["pass"]),
                            "rows_scored": len(cons)}
    tasks = []
    for jid, title in JOBS:
        for a, alabel in ARCHETYPES:
            votes = {m: (o["tasks"][jid][a] or {}).get("success") for m, o in models.items()}
            counted = [v for v in votes.values() if v != "n/a"]
            top = max(("Y", "Partial", "N"), key=lambda v: (counted.count(v), {"N": 2, "Partial": 1, "Y": 0}[v])) if counted else "n/a"
            tasks.append({"job": jid, "title": title, "archetype": a, "archetype_label": alabel, "votes": votes, "consensus": top,
                          "unanimous": len(set(counted)) <= 1, "a20_na": (jid, a) in NA_CELLS})
    return {"rows": rows, "surfaces": surf_means, "tasks": tasks, "fixes": fixes}
