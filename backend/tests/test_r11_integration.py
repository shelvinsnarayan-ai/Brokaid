"""R1.1 integration — hits live preview backend for jobs panel + notifications + regression."""
import os
import time
import uuid
import requests
import pytest

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://brokaid-demo.preview.emergentagent.com").rstrip("/")


def _sess(email: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/dev/impersonate", json={"email": email}, timeout=30)
    assert r.status_code == 200, f"impersonate {email}: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def olly():
    return _sess("operator@harbourline.example")


@pytest.fixture(scope="module")
def cass():
    time.sleep(1)
    return _sess("ae@harbourline.example")


@pytest.fixture(scope="module")
def ada():
    time.sleep(1)
    return _sess("principal@harbourline.example")


@pytest.fixture(scope="module")
def kai():
    time.sleep(1)
    return _sess("owner@fictionalclient.example")


@pytest.fixture(scope="module")
def bookkeeper():
    time.sleep(1)
    return _sess("bookkeeper@fictionalclient.example")


# ---- Jobs summary ----

def test_jobs_summary_shape_and_leader(olly):
    r = olly.get(f"{BASE}/api/o/jobs/summary", timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    counts = data.get("counts") or {}
    for k in ("queued", "running", "failed", "dead", "succeeded"):
        assert k in counts, f"missing counts.{k}: {data}"
        assert isinstance(counts[k], int)
    assert "oldest_queued_s" in data
    leader = data.get("leader") or {}
    assert leader.get("alive") is True, f"leader not alive: {leader}"
    for item in data.get("dead", []):
        assert "label" in item and item["label"]
        # plain words, no raw dotted kind keys
        assert "." not in item["label"], f"dead item label looks like a kind: {item}"
        assert "attempts" in item
    print("summary:", {"counts": counts, "oldest": data.get("oldest_queued_s"), "leader_alive": leader.get("alive"), "dead_count": len(data.get("dead", []))})


@pytest.mark.parametrize("who", ["cass", "ada", "kai"])
def test_jobs_summary_403_for_non_operator(who, request):
    s = request.getfixturevalue(who)
    r = s.get(f"{BASE}/api/o/jobs/summary", timeout=30)
    assert r.status_code == 403, f"{who} summary: {r.status_code}"


@pytest.mark.parametrize("who", ["cass", "ada", "kai"])
def test_jobs_retry_discard_403_for_non_operator(who, request):
    s = request.getfixturevalue(who)
    jid = "nonexistent"
    r1 = s.post(f"{BASE}/api/o/jobs/{jid}/retry", timeout=30)
    r2 = s.post(f"{BASE}/api/o/jobs/{jid}/discard", timeout=30)
    assert r1.status_code == 403, f"{who} retry: {r1.status_code}"
    assert r2.status_code == 403, f"{who} discard: {r2.status_code}"


def test_retry_discard_404_on_unknown_job(olly):
    jid = f"unknown-{uuid.uuid4().hex[:8]}"
    r1 = olly.post(f"{BASE}/api/o/jobs/{jid}/retry", timeout=30)
    r2 = olly.post(f"{BASE}/api/o/jobs/{jid}/discard", timeout=30)
    assert r1.status_code == 404, r1.text
    assert r2.status_code == 404, r2.text


# ---- Notifications: single client_question alert ----

def test_kai_question_produces_single_client_question(kai, cass):
    msg = f"R11 QA question - please ignore - {uuid.uuid4().hex[:6]}"
    r = kai.post(f"{BASE}/api/c/ask/message", json={"message": msg}, timeout=45)
    assert r.status_code == 200, r.text
    body = r.json()
    envelope_id = body.get("envelope_id")
    assert envelope_id, f"no envelope_id in ask response: {body}"
    print("envelope_id (thread):", envelope_id)
    # Poll Cass's notifications; link contains /o/inbox/{thread_id}
    found = []
    for _ in range(15):
        time.sleep(2)
        rn = cass.get(f"{BASE}/api/notifications?include_tests=1&limit=200", timeout=30)
        if rn.status_code != 200:
            continue
        items = (rn.json() or {}).get("notifications", [])
        found = [n for n in items
                 if n.get("kind") == "client_question" and envelope_id in (n.get("link") or "")]
        if found:
            break
    assert len(found) == 1, f"expected exactly 1 client_question for thread, got {len(found)}: {found}"
    assert found[0].get("count", 1) == 1, f"notification already aggregated: {found[0]}"


# ---- Client data access post-dedupe ----

def test_kai_still_sees_client_data(kai):
    # /c/home and /c/cover are frontend routes; equivalent client APIs:
    r1 = kai.get(f"{BASE}/api/c/me", timeout=30)
    r2 = kai.get(f"{BASE}/api/c/things", timeout=30)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text


def test_bookkeeper_still_sees_client_data(bookkeeper):
    r1 = bookkeeper.get(f"{BASE}/api/c/me", timeout=30)
    r2 = bookkeeper.get(f"{BASE}/api/c/things", timeout=30)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text


def test_bookkeeper_cannot_approve(bookkeeper, kai):
    # R1.3: /api/c/approvals never existed (test skipped forever); use the real approve route on an arranged envelope.
    from tests.test_r14_client_access import MOTOR, _approved_envelope
    eid = _approved_envelope()
    r2 = bookkeeper.post(f"{BASE}/api/c/matters/{MOTOR}/approve/{eid}", timeout=30)
    assert r2.status_code == 403, f"bookkeeper approve expected 403, got {r2.status_code}: {r2.text}"


# ---- Regression: operator lists still 200 and no 'Release Check' ----

@pytest.mark.parametrize("path", ["/api/o/clients", "/api/o/queue", "/api/o/weekly-pack", "/api/o/renewals"])
def test_operator_endpoints_regression(olly, path):
    r = olly.get(f"{BASE}{path}", timeout=30)
    assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:200]}"
    txt = r.text
    assert "Release Check" not in txt, f"{path} unexpectedly includes 'Release Check'"
