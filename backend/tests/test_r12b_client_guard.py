"""R1.2b fix — every /api/c route that names an object returns 404 for another client's object."""
import re
import uuid

import pytest

from app.db.tenant_safe import wrap
from app.kernel import auth_flows as af
from app.kernel.tenant_ctx import use_tenant
from tests.test_r12_auth import run
from tests.test_r12b import CLIENT, _client_session

ID_PARAMS = {"policy_id", "envelope_id", "certificate_id", "claim_id", "membership_id", "renewal_id"}


def _c_routes(app):
    out = []
    for r in app.routes:
        p = getattr(r, "path", "")
        if p.startswith("/api/c/") and "{" in p:
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                out.append((m, p))
    return out


async def _foreign(db, tid):
    """Objects belonging to a client Kai is NOT a member of."""
    safe = wrap(db)
    async with use_tenant(tid):
        p = await safe.policies.find_one({"client_id": {"$ne": CLIENT}})
        own = await safe.policies.find_one({"client_id": CLIENT})
        foreign_cid = p["client_id"]
        claim, mem = str(uuid.uuid4()), str(uuid.uuid4())
        await safe.claims.insert_one({"claim_id": claim, "client_id": foreign_cid, "policy_id": p["policy_id"], "status": "open"})
        await safe.memberships.insert_one({"membership_id": mem, "client_id": foreign_cid, "user_id": str(uuid.uuid4()),
                                           "role": "bookkeeper", "authority": {}, "revoked_at": None})
        renewal = str(uuid.uuid4())
        await safe.renewals.insert_one({"renewal_id": renewal, "client_id": foreign_cid, "policy_id": p["policy_id"], "stage": "info_requested"})
    env, cert, thread = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    sys = {"kind": "system", "id": "test", "name": "test"}
    await af.ledger(db, tid, "EnvelopeOpened", sys, {"envelope_id": env, "policy_id": p["policy_id"]})
    await af.ledger(db, tid, "CertificateRegenerated", sys, {"certificate_id": cert, "policy_id": p["policy_id"], "envelope_id": env})
    await af.ledger(db, tid, "ClientQuestionAsked", sys, {"envelope_id": thread, "client_id": foreign_cid, "message": "x"})
    return {"policy_id": p["policy_id"], "envelope_id": env, "certificate_id": cert, "claim_id": claim,
            "membership_id": mem, "thread_id": thread, "renewal_id": renewal}, own["policy_id"]


def test_every_c_route_with_an_object_id_hides_foreign_client_objects():
    async def go(db, c, app):
        tid = (await db.tenants.find_one({"slug": "harbourline"}))["tenant_id"]
        ids, own_policy = await _foreign(db, tid)
        kai, _ = await _client_session(db, "owner@fictionalclient.example")
        routes = _c_routes(app)
        assert len(routes) >= 15
        for method, path in routes:
            params = set(re.findall(r"{(\w+)}", path))
            assert params <= ID_PARAMS, f"new /api/c id param on {path}: add it to client_guard.ID_KEYS and this test"
            url = path
            for k in params:
                url = url.replace("{" + k + "}", ids[k])
            body = {"raw_message": "x", "idempotency_key": "g", "switch": "can_pay", "on": True, "holder": None}
            r = await c.request(method, url, cookies=kai, json=body if method != "GET" else None)
            assert r.status_code == 404 and r.json()["detail"] == "not_found", (method, url, r.status_code, r.text[:160])
        # ids carried in query strings / JSON bodies on id-less routes
        for method, url, body in (("POST", "/api/c/certificate/regen", {"policy_id": ids["policy_id"]}),
                                  ("POST", "/api/c/claims", {"description": "Water damage office", "policy_id": ids["policy_id"]}),
                                  ("POST", "/api/c/ask/seen", {"thread_id": ids["thread_id"]}),
                                  ("GET", f"/api/c/things?policy_id={ids['policy_id']}", None)):
            r = await c.request(method, url, cookies=kai, json=body)
            assert r.status_code == 404, (url, r.status_code, r.text[:160])
        # foreign request-change is 404, never an authority message about a client Kai isn't in
        r = await c.post(f"/api/c/matters/{ids['policy_id']}/request-change", cookies=kai, json={"raw_message": "x"})
        assert r.status_code == 404 and "can_request_change" not in r.text
        # own objects still work
        assert (await c.get(f"/api/c/matters/{own_policy}/status", cookies=kai)).status_code == 200
        assert (await c.get(f"/api/c/policies/{own_policy}/pending-quote", cookies=kai)).status_code == 200
        # unknown ids are indistinguishable from foreign ones
        assert (await c.get(f"/api/c/matters/{uuid.uuid4()}/status", cookies=kai)).json()["detail"] == "not_found"
    run(go)


@pytest.mark.parametrize("bad", ["", "x" * 200])
def test_resolve_rejects_malformed_ids(bad):
    import asyncio
    from app.middleware.client_guard import resolve_client
    assert asyncio.run(resolve_client(None, "policy_id", bad)) is None
