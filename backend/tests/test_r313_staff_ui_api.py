# R3.13 staff review — light API regression against the public preview URL (testing agent, iteration 47)
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
CLIENT = "00000000-0000-4000-8000-000000000001"
MOTOR = "00000000-0000-4000-8000-000000000104"
GOOD_PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
JS_PDF = b"%PDF-1.4 1 0 obj << /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >> endobj trailer<<>> %%EOF"


def _s(email):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/dev/impersonate", json={"email": email})
    if r.status_code != 200:
        pytest.skip(f"impersonate failed for {email}: {r.status_code}")
    return s


@pytest.fixture(scope="module")
def ae():
    return _s("ae@harbourline.example")


@pytest.fixture(scope="module")
def op():
    return _s("operator@harbourline.example")


# Policy docs: list RBAC flag
def test_list_can_edit_flags(ae, op):
    a = ae.get(f"{BASE_URL}/api/o/clients/{CLIENT}/policy-documents").json()
    o = op.get(f"{BASE_URL}/api/o/clients/{CLIENT}/policy-documents").json()
    assert a["can_edit"] is True and o["can_edit"] is False
    assert any(p["policy_id"] == MOTOR for p in a["policies"])


# Policy docs: JS PDF rejected, operator upload forbidden
def test_js_pdf_rejected_and_operator_forbidden(ae, op):
    r = ae.post(f"{BASE_URL}/api/o/policies/{MOTOR}/documents", data={"kind": "pds"},
                files={"file": ("TEST_js.pdf", JS_PDF, "application/pdf")})
    assert r.status_code == 422
    r = op.post(f"{BASE_URL}/api/o/policies/{MOTOR}/documents", data={"kind": "pds"},
                files={"file": ("TEST_op.pdf", GOOD_PDF, "application/pdf")})
    assert r.status_code == 403


# Policy docs: upload (non-current PDS) -> client sees -> withdraw -> client doesn't
def test_upload_client_visible_then_withdraw(ae):
    r = ae.post(f"{BASE_URL}/api/o/policies/{MOTOR}/documents", data={"kind": "wording"},
                files={"file": ("TEST_wording.pdf", GOOD_PDF, "application/pdf")})
    assert r.status_code == 200, r.text
    did = r.json()["document"]["doc_id"]
    try:
        cl = _s("owner@fictionalclient.example")
        assert cl.get(f"{BASE_URL}/api/c/documents/file/{did}").status_code == 200
        bad = ae.post(f"{BASE_URL}/api/o/policy-documents/{did}/withdraw", json={"reason": "ab"})
        assert bad.status_code == 422
    finally:
        w = ae.post(f"{BASE_URL}/api/o/policy-documents/{did}/withdraw", json={"reason": "TEST_ cleanup"})
        assert w.status_code == 200
    assert cl.get(f"{BASE_URL}/api/c/documents/file/{did}").status_code in (403, 404, 410)


# Queue: rows should carry a client name (KNOWN FAIL iteration 47: client_display always null)
def test_queue_has_client_display(ae):
    cards = ae.get(f"{BASE_URL}/api/o/queue").json()["cards"]
    if not cards:
        pytest.skip("empty queue")
    assert any(c.get("client_display") for c in cards if c.get("client_id")), "client_display null on every card"


# Alerts: grouping needs client_name (KNOWN FAIL iteration 47: all null -> one 'General' group)
def test_alerts_have_client_name(ae):
    n = ae.get(f"{BASE_URL}/api/notifications?limit=100").json()["notifications"]
    if not n:
        pytest.skip("no alerts")
    assert any(x.get("client_name") for x in n), "client_name null on every alert"
