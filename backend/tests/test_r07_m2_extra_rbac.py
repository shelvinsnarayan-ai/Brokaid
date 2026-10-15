"""R0.7 — M2 extra RBAC/live checks: client 403s, PDF 403s, operator weekly pack, drill log shape."""
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
    r = c.post("/api/auth/dev/impersonate", json={"email": email})
    assert r.status_code == 200, (email, r.status_code, r.text)
    return c


# Weekly pack / PDF 403s for client + AE
def test_weekly_pack_denied_for_client_and_ae():
    client = _as("owner@fictionalclient.example")
    assert client.get("/api/o/weekly-pack").status_code in (401, 403)
    assert client.get("/api/o/weekly-pack.pdf").status_code in (401, 403)
    ae = _as("ae@harbourline.example")
    assert ae.get("/api/o/weekly-pack").status_code == 403
    assert ae.get("/api/o/weekly-pack.pdf").status_code == 403


def test_weekly_pack_pdf_operator_ok():
    r = _as("operator@harbourline.example").get("/api/o/weekly-pack.pdf")
    assert r.status_code == 200
    assert r.content[:4] == b"%PDF"
    cd = r.headers.get("content-disposition", "")
    assert "harbourline" in cd.lower() and ".pdf" in cd.lower()


# Renewals + Inbox: client denied
def test_renewals_and_inbox_client_denied():
    client = _as("owner@fictionalclient.example")
    assert client.get("/api/o/renewals").status_code in (401, 403)
    assert client.get("/api/o/inbox").status_code in (401, 403)


# Drills: 403 for principal AND for AE; 200 for operator
def test_drills_rbac_principal_and_ae_denied():
    assert _as("principal@harbourline.example").get("/api/o/governance/drills").status_code == 403
    assert _as("ae@harbourline.example").get("/api/o/governance/drills").status_code == 403
    r = _as("operator@harbourline.example").get("/api/o/governance/drills")
    assert r.status_code == 200
    body = r.json()
    assert "drills" in body and isinstance(body["drills"], list)
    assert len(body["drills"]) <= 10


# POST drills denied for principal (keeps the log clean)
def test_drills_post_denied_for_principal():
    r = _as("principal@harbourline.example").post("/api/o/governance/drills", json={
        "agent": "intake", "engage_s": 0.1, "release_s": 0.1, "result": "pass",
        "engaged_at": "2026-10-15T00:00:00+00:00", "released_at": "2026-10-15T00:00:01+00:00"})
    assert r.status_code == 403


# Harbourline logins work; Burmac logins blocked
def test_old_burmac_logins_blocked():
    c = httpx.Client(base_url=BASE, timeout=30)
    for e in ("principal@burmac.example", "ae@burmac.example", "operator@burmac.example"):
        assert c.post("/api/auth/dev/impersonate", json={"email": e}).status_code != 200, e


# Weekly pack metric structure: 5 keys + data_basis on every one
def test_weekly_pack_metric_shape():
    body = _as("principal@harbourline.example").get("/api/o/weekly-pack").json()
    keys = [m["key"] for m in body["metrics"]]
    assert keys == ["promises_kept", "promises_open", "promises_overdue", "approvals", "spend"]
    for m in body["metrics"]:
        assert isinstance(m["value"], (int, float))
        assert m["data_basis"] in ("registry", "estimate")
    # spend is explicitly estimate
    spend = next(m for m in body["metrics"] if m["key"] == "spend")
    assert spend["data_basis"] == "estimate"
    assert "ESTIMATE" in spend["label"]


# Renewals row shape: dd/mm/yyyy expiry, non-negative days_to_go, promise + next_step strings
def test_renewals_row_shape():
    rows = _as("ae@harbourline.example").get("/api/o/renewals").json()["renewals"]
    assert rows
    for r in rows:
        d, m, y = r["expiry"].split("/")
        assert len(d) == 2 and len(m) == 2 and len(y) == 4
        assert r["days_to_go"]["value"] >= 0
        assert r["days_to_go"]["data_basis"] == "registry"
        assert isinstance(r["promise"], str) and r["promise"]
        assert isinstance(r["next_step"], str) and r["next_step"]


# Inbox row shape: thread/client/status/promise fields, no snake_case leaks in surface values
def test_inbox_row_shape():
    threads = _as("ae@harbourline.example").get("/api/o/inbox").json()["threads"]
    assert len(threads) >= 3
    seen_status = {t["status"] for t in threads}
    assert {"Awaiting broker reply", "Answered"} <= seen_status
    for t in threads:
        assert t.get("client") and t.get("status") and t.get("promise")
        # status should be human sentence, not snake_case key
        assert "_" not in t["status"]
