"""R2.0 live preview checks (hits REACT_APP_BACKEND_URL).

Skips if BROKAID_TEST_NO_SERVER != 1 (local in-proc test server has no QA users).
Uses dev impersonation (preview-only) for Harbourline demo client users and the
password+TOTP flow for operator/principal where needed.
"""
from __future__ import annotations

import hashlib
import os
import re
import time

import pyotp
import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
LIVE = os.environ.get("BROKAID_TEST_NO_SERVER") == "1" and BASE.startswith("http")

pytestmark = pytest.mark.skipif(not LIVE, reason="live preview test; needs BROKAID_TEST_NO_SERVER=1 + REACT_APP_BACKEND_URL")


def _impersonate(email: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/dev/impersonate", json={"email": email}, timeout=20)
    assert r.status_code == 200, (email, r.status_code, r.text[:300])
    return s


def _login_totp(email: str, password: str, totp_secret: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", json={"email": email, "password": password}, timeout=20)
    assert r.status_code == 200, (email, r.status_code, r.text[:300])
    code = pyotp.TOTP(totp_secret).now()
    r = s.post(f"{BASE}/api/auth/login/verify", json={"code": code}, timeout=20)
    assert r.status_code == 200, (email, r.status_code, r.text[:300])
    body = r.json()
    # Multi-tenant users get a chooser; pick a tenant if offered
    if isinstance(body, dict) and body.get("next") == "choose_tenant":
        tenants = body.get("tenants") or []
        # Prefer Harbourline when present
        pick = next((t for t in tenants if "harbour" in (t.get("slug", "") + t.get("display_name", "")).lower()), tenants[0] if tenants else None)
        if pick:
            s.post(f"{BASE}/api/auth/switch-tenant", json={"tenant_id": pick["tenant_id"]}, timeout=20)
    return s


# --------- Acceptance (client API) ----------

def test_acceptance_404_for_random_renewal():
    s = _impersonate("owner@fictionalclient.example")
    r = s.get(f"{BASE}/api/c/acceptance/renewal/00000000-0000-0000-0000-000000000000", timeout=20)
    assert r.status_code == 404


def _find_sign_envelope(s: requests.Session) -> tuple[str, str] | None:
    """Scan the client's policies/matters for any endorsement envelope at sign stage."""
    me = s.get(f"{BASE}/api/c/me", timeout=20)
    if me.status_code != 200:
        return None
    data = me.json()
    for p in data.get("policies", []) or []:
        pid = p.get("policy_id")
        if not pid:
            continue
        matters = s.get(f"{BASE}/api/c/matters/{pid}", timeout=20)
        if matters.status_code != 200:
            continue
        for m in matters.json().get("matters", []) or []:
            if m.get("stage") in ("sign", "paid") and m.get("envelope_id"):
                return pid, m["envelope_id"]
    return None


def test_acceptance_endorsement_contract_if_available():
    s = _impersonate("owner@fictionalclient.example")
    found = _find_sign_envelope(s)
    if not found:
        pytest.skip("No endorsement in sign/paid stage available on preview for this client")
    pid, env = found
    r = s.get(f"{BASE}/api/c/acceptance/endorsement/{env}", timeout=20)
    assert r.status_code == 200, r.text
    v = r.json()
    assert set(["title", "lines", "doc_sha256", "ready", "label", "accepted"]).issubset(v.keys())
    assert len(v["doc_sha256"]) == 64
    assert "brokAId" in v["label"]
    pdf = s.get(f"{BASE}/api/c/acceptance/endorsement/{env}/pdf", timeout=30)
    assert pdf.status_code == 200
    assert pdf.content[:4] == b"%PDF"
    assert hashlib.sha256(pdf.content).hexdigest() == v["doc_sha256"]

    # bookkeeper → 403
    s2 = _impersonate("bookkeeper@fictionalclient.example")
    body = {"typed_name": "x", "accepted": True, "doc_sha256": v["doc_sha256"]}
    r403 = s2.post(f"{BASE}/api/c/acceptance/endorsement/{env}", json=body, timeout=20)
    assert r403.status_code == 403, r403.text

    # tampered hash → 409
    bad = {**body, "doc_sha256": "0" * 64}
    r409 = s.post(f"{BASE}/api/c/acceptance/endorsement/{env}", json=bad, timeout=20)
    assert r409.status_code == 409

    # unticked accepted=false → 422
    r422 = s.post(f"{BASE}/api/c/acceptance/endorsement/{env}",
                  json={**body, "accepted": False}, timeout=20)
    assert r422.status_code == 422

    # mismatched name → 409 with code=name_mismatch
    rmm = s.post(f"{BASE}/api/c/acceptance/endorsement/{env}",
                 json={**body, "typed_name": "Totally Different Person"}, timeout=20)
    assert rmm.status_code == 409
    detail = rmm.json().get("detail")
    code = detail.get("code") if isinstance(detail, dict) else None
    assert code == "name_mismatch", rmm.text


# --------- Compliance library (operator/principal) ----------

@pytest.fixture(scope="module")
def op_session():
    return _login_totp("operator@harbourline.example", "Olly-Harbour-Preview-2026",
                       "XFO67AFNLSO5ROX5RZ5ZK7ONT6WUCAIO")


@pytest.fixture(scope="module")
def principal_session():
    return _login_totp("qa.principal@harbourline.example", "QA-Harbour-9a6f0108",
                       "3O7QRFKB5GGMYKANTTG54JJAWWCT2ZDW")


def test_library_listing_contains_pds_tmd_obtain(op_session):
    r = op_session.get(f"{BASE}/api/o/compliance/library", timeout=20)
    assert r.status_code == 200, r.text
    body = r.json()
    rows = body["documents"] if isinstance(body, dict) and "documents" in body else body
    by = {x["kind"]: x for x in rows}
    assert "PDS" in by and "TMD" in by
    assert by["PDS"]["status"] == "obtain_from_insurer"
    assert by["TMD"]["status"] == "obtain_from_insurer"


def test_direct_fsg_upload_rejected_409(op_session):
    r = op_session.post(f"{BASE}/api/o/compliance/documents",
                        data={"kind": "FSG", "version": "1", "effective_date": "2026-10-01"},
                        files={"file": ("fsg.pdf", b"%PDF-1.4 x", "application/pdf")}, timeout=30)
    assert r.status_code == 409, (r.status_code, r.text[:300])
    assert "library" in r.text.lower()


def test_operator_cannot_approve(op_session):
    r = op_session.get(f"{BASE}/api/o/compliance/library", timeout=20)
    body = r.json()
    rows = body["documents"] if isinstance(body, dict) and "documents" in body else body
    draft = next((x for x in rows if x.get("status") == "draft"), None)
    if not draft:
        pytest.skip("No draft present on Harbourline — library may already be approved/live")
    r2 = op_session.post(f"{BASE}/api/o/compliance/library/{draft['doc_id']}/approve",
                        json={"reviewer_name": "Rita", "confirmed": True}, timeout=20)
    assert r2.status_code == 403, (r2.status_code, r2.text[:300])


def test_regression_client_home_loads():
    s = _impersonate("owner@fictionalclient.example")
    r = s.get(f"{BASE}/api/c/me", timeout=20)
    assert r.status_code == 200, r.text


def test_regression_operator_queue_loads(op_session):
    r = op_session.get(f"{BASE}/api/o/queue", timeout=20)
    assert r.status_code == 200, (r.status_code, r.text[:200])
