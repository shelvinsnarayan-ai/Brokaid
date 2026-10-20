"""R3.0 Phase A: production access + naming — bootstrap beside demo operators, demo-host guard, generic sign-in error,
role-named personas, current-terms-only client Cover."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.tenant_safe import wrap
from app.kernel import auth_flows as af
from app.kernel import email_channel as ec
from app.kernel import release_check as rc
from app.kernel.tenant_ctx import use_tenant
from tests.test_r12_auth import run
from tests.test_r16_renewals import _people, _tenant


def test_bootstrap_invites_owner_even_when_demo_operator_exists():
    async def go(db, c, app):
        tid = (await db.tenants.find_one({"slug": "harbourline"}))["tenant_id"]
        assert await db.users.find_one({"tenant_id": tid, "email": "operator@harbourline.example"})  # demo Operator seeded
        await db.auth_tokens.delete_many({})
        await db.tenants.update_one({"tenant_id": tid}, {"$set": {"email_settings.allowlist": ["someone@else.example"]}})
        sent = []

        async def fake(**kw):
            sent.append(kw["to"])
            return "m"
        assert await af.bootstrap_operator(db, send=fake) == "issued"
        assert await af.bootstrap_operator(db, send=fake) == "skipped:invite_open"  # idempotent
        inv = await db.auth_tokens.find_one({"bootstrap": True, "tenant_id": tid})
        assert inv["email"] == "owner.boot@r30.example" and inv["role"] == "operator"
        t = await db.tenants.find_one({"tenant_id": tid})
        assert "owner.boot@r30.example" in t["email_settings"]["allowlist"]  # per-tenant allow-list now lets the invite out
    run(go, {"BOOTSTRAP_OPERATOR_EMAIL": "owner.boot@r30.example", "BOOTSTRAP_TENANT_SLUG": "harbourline"})


def test_demo_host_guard(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "preview")
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    monkeypatch.setenv("APP_BASE_URL", "https://brokaid.emergent.host")
    problem, host = rc.demo_host_problem()
    assert problem.startswith("DEMO MODE ON A NON-PREVIEW HOST") and host == "brokaid.emergent.host"
    monkeypatch.setenv("APP_BASE_URL", "https://brokaid-demo.preview.emergentagent.com")
    assert rc.demo_host_problem()[0] is None
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:3000")
    assert rc.demo_host_problem()[0] is None
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AUTH_DEV_MODE", "false")
    monkeypatch.setenv("APP_BASE_URL", "https://app.burmac.com.au")
    assert rc.demo_host_problem()[0] is None


def test_generic_sign_in_message_for_unknown_email_and_wrong_password():
    from app.routes.auth_login import BAD
    assert BAD == "We couldn't sign you in. Check your email and password — or, if you're new, ask your broker for an invite."

    async def go(db, c, app):
        a = await c.post("/api/auth/login", json={"email": "nobody@nowhere.example", "password": "Wrong-password-123"})
        b = await c.post("/api/auth/login", json={"email": "operator@harbourline.example", "password": "Wrong-password-123"})
        assert a.status_code == b.status_code == 401 and a.json()["detail"] == b.json()["detail"] == BAD
    run(go)


def test_greeting_and_persona_names():
    assert ec.greet_name("Client") == ec.greet_name("Client bookkeeper") == ec.greet_name("Operator") == "there"
    assert ec.greet_name("Shelvin Narayan") == "Shelvin" and ec.greet_name(None) == "there"
    from app.routes.auth_dev import DEMO_PERSONAS
    from app.seeds.seed_users import CLIENTS, OPERATORS
    shown = [n for _, n, *_ in DEMO_PERSONAS] + [u["display_name"] for u in OPERATORS + CLIENTS]
    for bad in ("Kai", "Ada", "Cass", "Olly", "Bea", "Pip"):
        assert not any(bad in s.split() for s in shown), bad
    assert [n for _, n, *_ in DEMO_PERSONAS] == ["Client", "Client bookkeeper", "Account executive", "Principal", "Operator"]


@pytest.mark.asyncio
async def test_client_cover_lists_current_terms_and_history_on_request(app_client, mongo_db):
    _, http = app_client
    db = mongo_db
    tid = await _tenant(db, f"r30-{uuid.uuid4().hex[:4]}")
    now = datetime.now(timezone.utc)
    async with use_tenant(tid):
        await wrap(db).clients.insert_one({"client_id": "c1", "display_name": "Harbour Cafe"})
        for pid, st, extra in (("cur", "managed", {}), ("old", "renewed", {}), ("can", "cancelled", {}), ("lap", "lapsed", {}),
                               ("arc", "archived", {}), ("tst", "managed", {"test": True})):
            await wrap(db).policies.insert_one({"policy_id": pid, "client_id": "c1", "status": st, "product": "Business Pack",
                                                "insurer_display": "Fictional Carrier A", "inception": now - timedelta(days=30),
                                                "expiry": now + timedelta(days=300), "schedule": {}, **extra})
    _, cookies = await _people(db, tid, "c1")
    d = (await http.get("/api/c/policies", headers=cookies["owner"])).json()
    assert [p["policy_id"] for p in d["policies"]] == ["cur"] and "previous" not in d
    h = (await http.get("/api/c/policies?include_history=1", headers=cookies["owner"])).json()
    assert [p["policy_id"] for p in h["policies"]] == ["cur"]
    assert sorted(p["policy_id"] for p in h["previous"]) == ["can", "lap", "old"]  # archived and test-flagged never shown
