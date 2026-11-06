"""R3.9 live-API checks against preview. Non-mutating where possible; does NOT burn push test budget."""
import os
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://brokaid-demo.preview.emergentagent.com").rstrip("/")

def _impersonate(email):
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/dev/impersonate", json={"email": email}, timeout=15)
    assert r.status_code == 200, f"impersonate {email}: {r.status_code} {r.text}"
    return s

def test_ae_status():
    s = _impersonate("ae@harbourline.example")
    r = s.get(f"{BASE}/api/me/push/status", timeout=15)
    assert r.status_code == 200, r.text
    j = r.json()
    print("AE status:", {k: j.get(k) for k in ("enabled","disabled_reason","prefs_push","tests_left","public_key")})
    print("AE devices:", len(j.get("devices") or []))
    assert "enabled" in j and "prefs_push" in j and "devices" in j and "tests_left" in j

def test_client_403_status_and_test():
    s = _impersonate("owner@fictionalclient.example")
    r = s.get(f"{BASE}/api/me/push/status", timeout=15)
    assert r.status_code == 403, r.text
    r2 = s.post(f"{BASE}/api/me/push/test", json={"scope": "all"}, timeout=15)
    assert r2.status_code == 403, r2.text
    print("client 403 status + test: OK")

def test_principal_and_operator_status():
    for email in ("principal@harbourline.example", "operator@harbourline.example"):
        s = _impersonate(email)
        r = s.get(f"{BASE}/api/me/push/status", timeout=15)
        assert r.status_code == 200, f"{email}: {r.status_code} {r.text}"
        print(f"{email} status OK; enabled={r.json().get('enabled')} tests_left={r.json().get('tests_left')}")

if __name__ == "__main__":
    test_ae_status()
    test_client_403_status_and_test()
    test_principal_and_operator_status()
    print("ALL PASS")
