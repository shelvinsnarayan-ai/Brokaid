"""R1.0 — live end-to-end release-check API runs (PASS + forced fail) and RBAC/isolation checks."""
import os
import time

import httpx
import pytest
from dotenv import dotenv_values
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values(ROOT / "frontend/.env").get("REACT_APP_BACKEND_URL")

pytestmark = pytest.mark.skipif(not BASE or os.environ.get("RUN_LIVE_RELEASE_CHECK") != "1",
                                reason="opt-in: real AI runs (~45s each); set RUN_LIVE_RELEASE_CHECK=1")


def _impersonate(email: str) -> httpx.Client:
    c = httpx.Client(base_url=BASE, timeout=90)
    r = c.post("/api/auth/dev/impersonate", json={"email": email})
    assert r.status_code == 200, f"impersonate {email} -> {r.status_code} {r.text[:200]}"
    return c


def _poll(c: httpx.Client, run_id: str, max_s: int = 180) -> dict:
    deadline = time.time() + max_s
    last = None
    while time.time() < deadline:
        time.sleep(5)
        r = c.get(f"/api/o/release-check/{run_id}")
        assert r.status_code == 200
        last = r.json()
        if last.get("status") == "done":
            return last
    raise AssertionError(f"run not done in {max_s}s; last: {last}")


def test_live_pass_run_and_concurrent_409():
    olly = _impersonate("operator@harbourline.example")
    # If an earlier run is still running, wait briefly.
    for _ in range(12):
        latest = olly.get("/api/o/release-check/latest").json().get("latest")
        if not latest or latest.get("status") != "running":
            break
        time.sleep(5)

    r = olly.post("/api/o/release-check", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "running" and body.get("run_id")
    run_id = body["run_id"]

    # concurrent attempt → 409
    r2 = olly.post("/api/o/release-check", json={})
    assert r2.status_code == 409, f"expected 409, got {r2.status_code} {r2.text[:200]}"

    done = _poll(olly, run_id, max_s=180)
    assert done["result"] == "PASS", f"not PASS: {done}"
    assert done["short_sha"] and done["env"] == "development"
    keys = {c["key"] for c in done["checks"]}
    required = {"build", "config.email", "config.push", "config.storage", "config.llm", "ledger",
                "spine.request", "spine.quote", "spine.broker", "access.bookkeeper",
                "spine.client_approval", "spine.paid", "spine.sign", "spine.packet",
                "spine.finalise", "spine.verifier", "access.client_on_o",
                "access.ae_governance", "notifications"}
    missing = required - keys
    assert not missing, f"missing steps: {missing}"
    for c in done["checks"]:
        assert c["status"] in ("pass", "warn", "info", "fail")
        assert isinstance(c["ms"], int)


def test_live_forced_fail_spine_sign():
    olly = _impersonate("operator@harbourline.example")
    # ensure no running
    for _ in range(24):
        latest = olly.get("/api/o/release-check/latest").json().get("latest")
        if not latest or latest.get("status") != "running":
            break
        time.sleep(5)
    r = olly.post("/api/o/release-check", json={"inject": "spine.sign"})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    done = _poll(olly, run_id, max_s=180)
    assert done["result"] == "FAIL", done
    assert done["failed_step"] and done["failed_step"]["label"] == "Sign"
    assert "Injected failure" in done["failed_step"]["detail"]
    keys = {c["key"] for c in done["checks"]}
    assert "spine.sign" in keys
    assert "spine.finalise" not in keys
    assert "spine.verifier" not in keys


def test_live_rbac_403_for_non_operators():
    olly = _impersonate("operator@harbourline.example")
    latest = olly.get("/api/o/release-check/latest").json()
    run_id = (latest.get("latest") or {}).get("run_id") or (latest.get("history") or [{}])[0].get("run_id")
    for email in ("ae@harbourline.example", "principal@harbourline.example", "owner@fictionalclient.example"):
        c = _impersonate(email)
        assert c.post("/api/o/release-check", json={}).status_code == 403
        assert c.get("/api/o/release-check/latest").status_code == 403
        if run_id:
            assert c.get(f"/api/o/release-check/{run_id}").status_code == 403


def test_live_synthetic_hidden_from_normal_views():
    cass = _impersonate("ae@harbourline.example")
    for path in ("/api/o/clients", "/api/o/queue", "/api/o/renewals", "/api/o/inbox"):
        r = cass.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
        assert "Release Check" not in r.text, f"synthetic leaked into {path}"
    ada = _impersonate("principal@harbourline.example")
    r = ada.get("/api/o/weekly-pack")
    assert r.status_code == 200
    assert "Release Check" not in r.text
    for email in ("ae@harbourline.example", "principal@harbourline.example", "owner@fictionalclient.example"):
        c = _impersonate(email)
        r = c.get("/api/notifications", params={"limit": 50})
        assert r.status_code == 200
        assert "Release Check" not in r.text


def test_live_history_has_both_runs():
    olly = _impersonate("operator@harbourline.example")
    body = olly.get("/api/o/release-check/latest").json()
    assert "history" in body and len(body["history"]) <= 10
    # at least one PASS and one FAIL in recent history
    results = [h.get("result") for h in body["history"]]
    assert "PASS" in results
    assert "FAIL" in results
