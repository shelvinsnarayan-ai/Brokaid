"""R3.12 client fixes: people presets + invite (single use, expiry, TOTP, remove ends access), cross-client 404, documents hidden
server-side, renewal values through policy_field_guard, explainer copy passes the advice guard."""
import re
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.auth.dispatch import create_session_scoped
from app.auth.sessions import COOKIE_NAME
from app.db.tenant_safe import wrap
from app.kernel import advice_boundary, renewals as rn
from app.kernel.authority import require_authority
from app.kernel.client_library import client_visible
from app.kernel.tenant_ctx import use_tenant
from app.routes.c_people import Rights, plain, resolve
from tests.test_r12_auth import _accept, _events, _tid, run

CID = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-0000000003c2"


def test_presets_and_plain_words():
    assert resolve("bookkeeper", None)[1] | {} == {**resolve("read_only", None)[1], "can_pay": True}
    role, a = resolve("bookkeeper", None)
    assert role == "bookkeeper" and a["can_pay"] and a["can_view"] and not a["can_lodge_claim"] and not a["can_approve"]
    assert resolve("co_owner", None)[0] == "owner" and plain(resolve("co_owner", None)[1]) == "Can do everything"
    assert plain(resolve("read_only", None)[1]) == "Can see cover and documents"
    _, c = resolve("custom", Rights(can_lodge_claim=True, can_accept=True))
    assert c["can_lodge_claim"] and c["can_approve"] and c["can_sign"] and not c["can_pay"]
    assert plain(resolve("bookkeeper", None)[1]) == "Can see cover and documents and pay"


def test_renewal_values_go_through_the_guard():
    p = {"schedule": {"cover_types": [{"type": "Contents", "sum_insured": 120000}],
                      "vehicles": [{"rego": "DEF-456", "agreed_value": 28000}]}}
    ids = [v["id"] for v in rn.key_values(p)]
    assert ids == ["limits.contents", "vehicles[DEF-456].agreed_value"]
    ok, issues, facts = rn.assess_answers(p, {"values": {"items": {
        "limits.contents": {"confirmed": False, "requested": "150,000"}, "vehicles[DEF-456].agreed_value": {"confirmed": True},
        "limits.made_up": {"confirmed": False, "requested": "5"}}}})
    assert [c["field_path"] for c in ok] == ["schedule.limits.contents"] and ok[0]["request"] is True
    assert facts["values"] == {"confirmed": 1, "requested": 1}
    assert any("broker to review" in i for i in issues) and any("unknown value" in i for i in issues)  # never a direct edit
    _, issues, _ = rn.assess_answers(p, {"values": {"items": {"limits.contents": {"confirmed": False, "requested": "lots"}}}})
    assert issues and "amount" in issues[0]


def test_explainer_copy_is_general_information():
    src = (Path(__file__).resolve().parents[2] / "frontend/src/pages/client/explainers.ts").read_text()
    bodies = re.findall(r'body: "([^"]+)"', src)
    assert len(bodies) == 8
    for b in bodies:
        assert advice_boundary.classify_reply(b)["tier"] != "P", b
        assert not re.search(r"\byou should\b|\bwe recommend\b|\bguarantee", b, re.I)


def test_internal_kinds_hidden():
    assert client_visible({"kind": "PDS"}) and not client_visible({"kind": "CHANGE_PACKET"})
    assert not client_visible({"kind": "PDS", "staff_only": True}) and not client_visible({"kind": "BINDING"})


async def _client_cookie(db, tid, uid, cid, role="client_owner"):
    s = await create_session_scoped(db, tid, user_id=uid, client_id=cid, active_role=role, mfa_verified=True, ttl=timedelta(minutes=30))
    return {COOKIE_NAME: s.session_id}, s


def test_invite_bookkeeper_end_to_end():
    async def go(db, c, app):
        tid = await _tid(db)
        async with use_tenant(tid):
            owner = await wrap(db).users.find_one({"email": "owner@fictionalclient.example"})
            await wrap(db).clients.insert_one({"client_id": OTHER, "display_name": "Other Co"})
            await wrap(db).users.insert_one({"user_id": "u-other", "email": "o@other.example", "kind": "client", "display_name": "O"})
            await wrap(db).memberships.insert_one({"membership_id": "m-other", "client_id": OTHER, "user_id": "u-other", "role": "owner",
                                                   "authority": {}, "revoked_at": None})
            await wrap(db).user_client_links.insert_one({"user_id": "u-other", "client_id": OTHER, "role": "client_owner"})
        jar, _ = await _client_cookie(db, tid, owner["user_id"], CID)
        r = await c.post("/api/c/people/invite", cookies=jar, json={"name": "Bea Books", "email": "bea@books.example", "preset": "bookkeeper"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["email_sent"] is False and "No email was sent" in body["email_note"]
        token = body["invite_link"].rsplit("/", 1)[1]
        async with use_tenant(tid):
            tok = await wrap(db).system("auth_tokens").find_one({"token_id": body["invite_id"]})
        assert tok["token_hash"] != token and token not in str(tok)  # hashed, raw never stored
        assert tok["expires_at"] - tok["created_at"] == timedelta(hours=72)
        ev = await _events(db, tid, "ClientMemberInvited")
        assert ev and token not in str(ev[-1]["payload"])
        # TOTP enrolment is part of acceptance; the link is single use
        bk_jar, _secret, _ = await _accept(c, token)
        assert (await c.post(f"/api/auth/invite/{token}/accept", json={"password": "Harbour-Ferry-Lantern-42"})).status_code == 410
        people = (await c.get("/api/c/people", cookies=bk_jar)).json()
        assert not people["can_manage"] and people["invites"] == []
        assert (await c.post("/api/c/people/invite", cookies=bk_jar, json={"name": "X", "email": "x@x.example", "preset": "read_only"})).status_code == 403
        async with use_tenant(tid):
            bea = await wrap(db).users.find_one({"email": "bea@books.example"})
            m = await wrap(db).memberships.find_one({"user_id": bea["user_id"], "client_id": CID, "revoked_at": None})
        _, sess = await _client_cookie(db, tid, bea["user_id"], CID, "client_contact")
        await require_authority(db, sess, client_id=CID, action="pay_attempt", needed="can_pay")
        with pytest.raises(HTTPException) as e:
            await require_authority(db, sess, client_id=CID, action="claim_attempt", needed="can_lodge_claim")
        assert e.value.status_code == 403
        # other client's owner: 404 on our member and our documents
        ojar, _ = await _client_cookie(db, tid, "u-other", OTHER)
        assert (await c.put(f"/api/c/people/{m['membership_id']}/rights", cookies=ojar, json={"preset": "read_only"})).status_code == 404
        assert (await c.post(f"/api/c/members/{m['membership_id']}/remove", cookies=ojar)).status_code == 404
        # owner changes rights, then removes: access ends
        r = await c.put(f"/api/c/people/{m['membership_id']}/rights", cookies=jar, json={"preset": "read_only"})
        assert r.status_code == 200 and r.json()["can"] == "Can see cover and documents"
        assert (await c.post(f"/api/c/members/{m['membership_id']}/remove", cookies=jar)).status_code == 200
        assert (await c.get("/api/c/people", cookies=bk_jar)).status_code == 401
        # expired invite → 410
        r = await c.post("/api/c/people/invite", cookies=jar, json={"name": "Late", "email": "late@x.example", "preset": "read_only"})
        async with use_tenant(tid):
            await wrap(db).system("auth_tokens").update_one({"token_id": r.json()["invite_id"]},
                                                            {"$set": {"expires_at": tok["created_at"] - timedelta(days=1)}})
        late = r.json()["invite_link"].rsplit("/", 1)[1]
        assert (await c.post(f"/api/auth/invite/{late}/accept", json={"password": "Harbour-Ferry-Lantern-42"})).status_code == 410
        # documents: internal rows hidden from library and download; foreign client 404 on file
        async with use_tenant(tid):
            await wrap(db).doc_deliveries.insert_one({"delivery_id": "d-int", "doc_id": "x", "kind": "CHANGE_PACKET", "client_id": CID,
                                                      "context": "policy:p", "at": tok["created_at"]})
            await wrap(db).policy_documents.insert_one({"doc_id": "pd-staff", "client_id": CID, "policy_id": "00000000-0000-4000-8000-000000000101",
                                                        "kind": "schedule", "current": True, "title": "Binder", "staff_only": True,
                                                        "storage_path": "x", "filename": "x.pdf"})
            await wrap(db).policy_documents.insert_one({"doc_id": "pd-ok", "client_id": CID, "policy_id": "00000000-0000-4000-8000-000000000101",
                                                        "kind": "schedule", "current": True, "title": "Policy schedule", "storage_path": "x",
                                                        "filename": "x.pdf"})
        lib = (await c.get("/api/c/documents/library", cookies=jar)).text
        assert "pd-ok" in lib and "pd-staff" not in lib and "d-int" not in lib and "sha256" not in lib
        assert "d-int" not in (await c.get("/api/c/documents", cookies=jar)).text
        assert (await c.get("/api/c/documents/download?d=d-int", cookies=jar)).status_code == 404
        assert (await c.get("/api/c/documents/file/pd-staff", cookies=jar)).status_code == 404
        assert (await c.get("/api/c/documents/file/pd-ok", cookies=ojar)).status_code == 404
    run(go, {"EMAIL_AUTH_ENABLED": "false"})
