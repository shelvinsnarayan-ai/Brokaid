"""R0.6 follow-up — standalone operator login (live API).

Ada (principal) → 403 on agent operations; Olly (operator) → 200; /api/c/me role=operator.
"""
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
    assert r.status_code == 200, r.text
    return c


def test_principal_denied_operator_allowed_on_kill_switch():
    assert _as("principal@burmac.example").get("/api/o/kill-switch").status_code == 403
    olly = _as("operator@harbourline.example")
    assert olly.get("/api/o/kill-switch").status_code == 200
    assert olly.get("/api/o/registry").status_code == 200


def test_me_reports_operator_role():
    me = _as("operator@harbourline.example").get("/api/c/me").json()
    assert me["session"]["active_role"] == "operator"
