"""R3.11: consolidation maths (mean/min/max/spread, disagreement, baseline delta, pass bar, ranked fixes, task consensus), JSON validation,
and /api/o/reviews RBAC (Operator + Principal; AE and client 403)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.db.tenant_safe import wrap
from app.kernel import reviews as rv
from app.kernel.tenant_ctx import use_tenant
from tests.test_r34_claim_threads import _staff_cookie
from tests.test_r35_claim_attachments import BLOBS, _setup, fake_storage  # noqa: F401

IDS = {"C-HOME-390", "B-AE-INBOX-1280"}


def _out(score: int, fix="Do X", task="Y", broker_cov="n/a"):
    h = {}
    for key, _l, _c, bb in rv.ROWS:
        h[key] = {s: {"score": score, "evidence": ["C-HOME-390"], "reasoning": "r", "fix": fix} for s in rv.SURFACES}
        if bb is None:
            h[key]["broker"] = {"score": broker_cov, "evidence": [], "reasoning": "n/a", "fix": ""}
    return {"heuristics": h, "tasks": {j: {a: {"success": task, "measured_taps": 2, "friction": "f"} for a, _ in rv.ARCHETYPES} for j, _ in rv.JOBS}}


def test_validate():
    assert rv.validate(_out(3), IDS) == []
    bad = _out(3)
    bad["heuristics"]["help"]["client"]["score"] = 5
    bad["heuristics"]["help"]["broker"]["evidence"] = ["C-NOPE-390"]
    bad["tasks"]["J3"]["farmer"]["success"] = "maybe"
    errs = rv.validate(bad, IDS)
    assert any("help.client.score" in e for e in errs) and any("unknown ids" in e for e in errs) and any("J3.farmer" in e for e in errs)
    ins = _out(3)
    ins["heuristics"]["help"]["client"] = {"score": rv.INSUFFICIENT, "evidence": [], "reasoning": "", "fix": ""}
    assert rv.validate(ins, IDS) == []
    assert rv.validate({"x": 1}, IDS) == ["missing object 'heuristics'"]


def test_consolidate_maths():
    a, b, c, d = _out(1, "fix A"), _out(3), _out(4), _out(2, "fix D", task="N")
    a["heuristics"]["help"]["client"]["score"] = rv.INSUFFICIENT  # excluded from stats
    con = rv.consolidate({"A": a, "B": b, "C": c, "D": d})
    row = next(r for r in con["rows"] if r["row"] == "visibility" and r["surface"] == "client")
    assert row["scores"] == {"A": 1, "B": 3, "C": 4, "D": 2} and row["mean"] == 2.5 and (row["min"], row["max"], row["spread"]) == (1, 4, 3)
    assert row["disagreement"] and row["pass"] is False and row["baseline"] == 1 and row["delta"] == 1.5
    help_c = next(r for r in con["rows"] if r["row"] == "help" and r["surface"] == "client")
    assert help_c["n"] == 3 and help_c["mean"] == 3.0 and help_c["pass"] is True and help_c["spread"] == 2 and help_c["disagreement"]
    cov_b = next(r for r in con["rows"] if r["row"] == "am_i_covered" and r["surface"] == "broker")
    assert cov_b["mean"] is None and cov_b["pass"] is None and cov_b["baseline"] is None
    # fixes: only models scoring <3 count; priority = frequency × (3.0 − mean)
    vis = next(f for f in con["fixes"] if f["row"] == "visibility" and f["surface"] == "client")
    assert vis["frequency"] == 2 and vis["gap"] == 0.5 and vis["priority"] == 1.0 and set(vis["fixes"]) == {"A", "D"}
    assert not any(f["row"] == "help" and f["surface"] == "client" for f in con["fixes"])  # mean 3.0 → no gap
    assert con["fixes"] == sorted(con["fixes"], key=lambda f: (-f["priority"], -f["gap"], f["row"], f["surface"]))
    s = con["surfaces"]["client"]
    assert s["per_model"]["B"] == 3.0 and s["baseline"] == 1.3
    t = next(x for x in con["tasks"] if x["job"] == "J1" and x["archetype"] == "professional")
    assert t["consensus"] == "Y" and not t["unanimous"] and t["a20_na"]
    tie = rv.consolidate({"A": _out(3, task="Y"), "B": _out(3, task="N")})["tasks"][0]
    assert tie["consensus"] == "N"  # ties resolve to the more cautious verdict


@pytest.mark.asyncio
async def test_reviews_rbac(app_client, mongo_db):
    _, http = app_client
    tid, _o, _c, _f, ids, cookies, _other = await _setup(mongo_db)
    async with use_tenant(tid):
        await wrap(mongo_db).reviews.insert_one({"review_id": "R-T", "title": "t", "created_at": datetime.now(timezone.utc), "sha": "x", "models": [],
                                                 "consolidated": rv.consolidate({"A": _out(3)}), "files": {"pdf": "p", "md": "m"}, "raw": {}, "evidence": {},
                                                 "tasks_measured": []})
    for role, uid in (("operator", str(uuid.uuid4())), ("principal", ids["principal"])):
        h = await _staff_cookie(mongo_db, tid, uid, role)
        assert (await http.get("/api/o/reviews", headers=h)).json()["reviews"][0]["review_id"] == "R-T"
        assert (await http.get("/api/o/reviews/R-T", headers=h)).status_code == 200
        assert (await http.get("/api/o/reviews/R-T/download/raw-nope", headers=h)).status_code == 404
    ae = await _staff_cookie(mongo_db, tid, ids["ae"], "ae")
    for h in (ae, cookies["owner"]):
        assert (await http.get("/api/o/reviews", headers=h)).status_code == 403
        assert (await http.get("/api/o/reviews/R-T/download/pdf", headers=h)).status_code == 403
