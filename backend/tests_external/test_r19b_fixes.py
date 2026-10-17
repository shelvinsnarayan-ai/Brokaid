"""R1.9b — retest the 3 fixes made after iteration_29.

(1) /api/c/* returns 404 for platform admin (was 200 for /me, /things)
(2) Ops backups drill endpoint still PASSes (drill testid uniqueness is UI; backend just needs to produce drills)
(3) Wizard still creates tenants; invalid ABN still rejected (frontend grid layout is UI)
"""
import os
import time
import pyotp
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
EMAIL = "qa.platform@harbourline.example"
PASSWORD = "QA-Harbour-9a6f0108"
TOTP_SECRET = "3O7QRFKB5GGMYKANTTG54JJAWWCT2ZDW"


@pytest.fixture(scope="module")
def platform_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text
    # TOTP step
    code = pyotp.TOTP(TOTP_SECRET).now()
    r = s.post(f"{BASE_URL}/api/auth/login/verify", json={"code": code})
    if r.status_code != 200:
        time.sleep(31)
        code = pyotp.TOTP(TOTP_SECRET).now()
        r = s.post(f"{BASE_URL}/api/auth/login/verify", json={"code": code})
    assert r.status_code == 200, r.text
    return s


# ----- Fix 1: /api/c/* => 404 for platform admin -----
class TestClientRouterPlatformAdmin:
    @pytest.mark.parametrize("path", ["/api/c/me", "/api/c/things", "/api/c/claims"])
    def test_c_routes_return_404(self, platform_session, path):
        r = platform_session.get(f"{BASE_URL}{path}")
        assert r.status_code == 404, f"{path} -> {r.status_code} {r.text[:200]}"

    def test_o_clients_still_403(self, platform_session):
        r = platform_session.get(f"{BASE_URL}/api/o/clients")
        assert r.status_code == 403

    def test_platform_tenants_still_200(self, platform_session):
        r = platform_session.get(f"{BASE_URL}/api/platform/tenants")
        assert r.status_code == 200
        data = r.json()
        assert "tenants" in data


# ----- Fix 2: Ops drill endpoint still returns PASS result -----
class TestOpsDrill:
    def test_backups_run_then_drill_harbourline_pass(self, platform_session):
        # ensure a fresh backup exists for harbourline
        r = platform_session.post(f"{BASE_URL}/api/platform/backups/run", json={})
        assert r.status_code == 200, r.text
        backups = r.json().get("backups") or []
        # find harbourline tenant_id from backups
        hb = next((b for b in backups if b.get("tenant_slug") == "harbourline"), None)
        assert hb, f"no harbourline backup created: {backups}"
        tenant_id = hb["tenant_id"]
        r = platform_session.post(f"{BASE_URL}/api/platform/backups/drill/{tenant_id}", json={})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("result") == "PASS", f"drill not PASS: {body}"


# ----- Fix 3: Wizard valid/invalid ABN still works -----
class TestWizard:
    def test_invalid_abn_rejected(self, platform_session):
        payload = {"slug": "qa-r19b-badabn", "legal_name": "Bad ABN Co", "abn": "12345678901",
                   "afsl": "123456", "state": "NSW", "timezone": "Australia/Sydney",
                   "sender_name": "QA", "reply_to": "qa@example.com",
                   "operator_email": "qa-op@example.com", "operator_name": "Op", "test": True}
        r = platform_session.post(f"{BASE_URL}/api/platform/tenants", json=payload)
        assert r.status_code >= 400, r.text
        assert "abn" in r.text.lower() or "checksum" in r.text.lower()

    def test_valid_abn_creates(self, platform_session):
        import random, string
        slug = "qa-r19b-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        payload = {"slug": slug, "legal_name": "QA R19b Brokers", "abn": "51824753556",
                   "afsl": "123456", "state": "NSW", "timezone": "Australia/Sydney",
                   "sender_name": "QA", "reply_to": "qa@example.com",
                   "operator_email": f"qa-op-{slug}@example.com", "operator_name": "Op", "test": True}
        r = platform_session.post(f"{BASE_URL}/api/platform/tenants", json=payload)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("slug") == slug
