"""R0.7 — M2 carry stubs (live API): weekly pack, renewals, inbox, drill log, Harbourline emails."""
import os
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("REACT_APP_BACKEND_URL") or dotenv_values(ROOT / "frontend/.env").get("REACT_APP_BACKEND_URL")
pytestmark = pytest.mark.skipif(not BASE, reason="live API not configured")


def _as(email: str) -> httpx.Client:
    c = httpx.Client(base_url=BASE, timeout=30)
    assert c.post("/api/auth/dev/impersonate", json={"email": email}).status_code == 200
    return c


def test_weekly_pack_roles_and_basis():
    for who in ("principal", "operator"):
        r = _as(f"{who}@harbourline.example").get("/api/o/weekly-pack")
        assert r.status_code == 200
        assert {m["key"] for m in r.json()["metrics"]} == {
            "promises_kept", "promises_open", "promises_overdue", "approvals", "spend"}
        assert all(m["data_basis"] for m in r.json()["metrics"])
    pdf = _as("principal@harbourline.example").get("/api/o/weekly-pack.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert _as("ae@harbourline.example").get("/api/o/weekly-pack").status_code == 403


def test_renewals_sorted_and_client_denied():
    rows = _as("ae@harbourline.example").get("/api/o/renewals").json()["renewals"]
    assert rows
    keys = [tuple(reversed(r["expiry"].split("/"))) for r in rows]
    assert keys == sorted(keys)
    assert all(r["days_to_go"]["data_basis"] == "registry" and r["days_to_go"]["value"] >= 0 for r in rows)
    assert _as("owner@fictionalclient.example").get("/api/o/renewals").status_code == 403


def test_inbox_threads():
    r = _as("ae@harbourline.example").get("/api/o/inbox")
    assert r.status_code == 200
    threads = r.json()["threads"]
    assert len(threads) >= 3
    assert {t["status"] for t in threads} <= {"Overdue", "Due today", "Awaiting reply", "Answered"}
    assert "Answered" in {t["status"] for t in threads}


def test_drills_operator_only():
    # Read-only: only scripts/kill_switch_drill.py records drills (keeps the G6 log clean).
    drills = _as("operator@harbourline.example").get("/api/o/governance/drills").json()["drills"]
    assert len(drills) <= 10
    assert all({"engaged_at", "released_at", "operator", "package_label", "result"} <= set(d) for d in drills)
    ada = _as("principal@harbourline.example")
    assert ada.get("/api/o/governance/drills").status_code == 403
    assert ada.post("/api/o/governance/drills", json={}).status_code == 403


def test_harbourline_logins_renamed():
    assert _as("principal@harbourline.example")
    c = httpx.Client(base_url=BASE, timeout=30)
    assert c.post("/api/auth/dev/impersonate", json={"email": "principal@burmac.example"}).status_code != 200
