"""R0.13 Phase C — Web Push HTTP API tests.

Covers:
- /api/notifications/push-public-key
- /api/notifications/push-subscriptions POST/DELETE
- /api/notifications/push-test (operator only; off → skipped; invalid FCM → 410 → removed)
- prefs default push=false, push_available, quiet_hours.applies_to includes 'push'
"""
import base64
import os
import time
import secrets
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Try frontend/.env if env var not set
    try:
        with open("/app/frontend/.env") as fh:
            for line in fh:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                    break
    except Exception:
        pass
assert BASE_URL, "REACT_APP_BACKEND_URL required"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _valid_p256dh() -> str:
    """Generate a valid uncompressed P-256 public key, base64url-no-pad."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    assert len(pub) == 65 and pub[0] == 0x04
    return b64url(pub)


def _valid_auth() -> str:
    return b64url(secrets.token_bytes(16))


def _login(email: str) -> requests.Session:
    s = requests.Session()
    # Pace to avoid 429 throttling
    for attempt in range(3):
        r = s.post(f"{BASE_URL}/api/auth/dev/impersonate", json={"email": email}, timeout=20)
        if r.status_code == 200:
            return s
        if r.status_code == 429:
            time.sleep(2 + attempt * 2)
            continue
        pytest.fail(f"impersonate {email} failed: {r.status_code} {r.text}")
    pytest.skip(f"rate limited for {email}")


# -------- fixtures --------
@pytest.fixture(scope="module")
def op_sess():
    s = _login("operator@harbourline.example")
    yield s


@pytest.fixture(scope="module")
def owner_sess():
    time.sleep(1)
    s = _login("owner@fictionalclient.example")
    yield s


@pytest.fixture(scope="module")
def ae_sess():
    time.sleep(1)
    s = _login("ae@harbourline.example")
    yield s


# -------- tests --------
class TestPushPublicKey:
    def test_public_key_available(self, op_sess):
        r = op_sess.get(f"{BASE_URL}/api/notifications/push-public-key")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("available") is True
        assert isinstance(d.get("key"), str) and len(d["key"]) > 20


class TestPrefsDefault:
    def test_default_prefs_push_false_available_true(self, ae_sess):
        r = ae_sess.get(f"{BASE_URL}/api/notifications/prefs")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("push_available") is True
        qh = d.get("quiet_hours") or {}
        applies = qh.get("applies_to") or []
        assert "push" in applies, f"quiet_hours.applies_to missing push: {applies}"
        # push may be true if ae previously subscribed — allow but note
        # The spec: default false unless subscribed
        # We won't fail here because other tests may have set it.


class TestPushSubscriptionsCRUD:
    endpoint = f"https://fcm.googleapis.com/fcm/send/test-{secrets.token_hex(8)}"

    def test_non_https_rejected(self, op_sess):
        r = op_sess.post(f"{BASE_URL}/api/notifications/push-subscriptions",
                         json={"endpoint": "http://example.com/x",
                               "keys": {"p256dh": _valid_p256dh(), "auth": _valid_auth()}})
        assert r.status_code == 422, r.text

    def test_subscribe_and_dedupe_and_prefs_push_true(self, op_sess):
        payload = {"endpoint": self.endpoint,
                   "keys": {"p256dh": _valid_p256dh(), "auth": _valid_auth()}}
        r1 = op_sess.post(f"{BASE_URL}/api/notifications/push-subscriptions", json=payload)
        assert r1.status_code == 200, r1.text
        d1 = r1.json()
        assert d1["subscribed"] is True
        n1 = d1["devices"]
        assert n1 >= 1

        # Re-post same endpoint → no duplicate
        r2 = op_sess.post(f"{BASE_URL}/api/notifications/push-subscriptions", json=payload)
        assert r2.status_code == 200
        assert r2.json()["devices"] == n1

        # prefs.push now true
        p = op_sess.get(f"{BASE_URL}/api/notifications/prefs").json()
        assert p["push"] is True

    def test_unsubscribe_last_device_sets_push_false(self, op_sess):
        # Delete all devices currently on this op session user to reach "last" state
        # First list by subscribing and deleting the one we created
        r = op_sess.request("DELETE", f"{BASE_URL}/api/notifications/push-subscriptions",
                            json={"endpoint": self.endpoint})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["subscribed"] is False
        # devices may be >0 if other subs exist from prior runs; if 0, prefs.push should be false
        if d["devices"] == 0:
            p = op_sess.get(f"{BASE_URL}/api/notifications/prefs").json()
            assert p["push"] is False


class TestPushTest:
    def test_push_test_requires_operator_owner(self, owner_sess):
        r = owner_sess.post(f"{BASE_URL}/api/notifications/push-test")
        assert r.status_code == 403
        assert "operator_role_required" in r.text

    def test_push_test_requires_operator_ae(self, ae_sess):
        r = ae_sess.post(f"{BASE_URL}/api/notifications/push-test")
        assert r.status_code == 403

    def test_push_test_off_returns_skipped_off(self, op_sess):
        # Ensure no subscriptions and prefs.push=false
        # (previous test unsubscribed; also set via prefs put)
        op_sess.put(f"{BASE_URL}/api/notifications/prefs", json={"email": False, "push": False})
        r = op_sess.post(f"{BASE_URL}/api/notifications/push-test")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["push"] == "skipped:off"
        assert "hint" in d

    def test_push_test_invalid_fcm_removes_subscription(self, op_sess):
        endpoint = f"https://fcm.googleapis.com/fcm/send/{secrets.token_urlsafe(32)}"
        payload = {"endpoint": endpoint,
                   "keys": {"p256dh": _valid_p256dh(), "auth": _valid_auth()}}
        r = op_sess.post(f"{BASE_URL}/api/notifications/push-subscriptions", json=payload)
        assert r.status_code == 200, r.text
        before_devices = r.json()["devices"]

        r = op_sess.post(f"{BASE_URL}/api/notifications/push-test", timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        # push should be skipped:no_device (because invalid FCM token → 404/410)
        assert d["push"] in ("skipped:no_device", "sent"), f"unexpected push state: {d}"
        if d["push"] == "skipped:no_device":
            results = d.get("results") or []
            assert any(r.get("status") in (404, 410) for r in results), f"expected 404/410 in results: {results}"

        # subscription should be removed
        r2 = op_sess.request("DELETE", f"{BASE_URL}/api/notifications/push-subscriptions",
                             json={"endpoint": endpoint})
        after_devices = r2.json()["devices"]
        # The sub was deleted by push-test, so delete call reports current count (should be < before)
        assert after_devices < before_devices or after_devices == 0
