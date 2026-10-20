"""R2.3: business details → filled drafts → Principal approval → clean live render."""
from __future__ import annotations

import re
from datetime import timedelta

import pytest

from app.auth import sessions as sess
from app.auth.dispatch import create_session_scoped
from app.db.tenant_safe import wrap
from app.kernel import business_details as bd
from app.kernel import compliance_library as lib
from app.kernel import compliance_templates as T
from app.kernel import sandbox
from app.kernel.tenant_ctx import use_tenant

GOOD = {"legal_name": "Sandbox Brokers Pty Ltd", "trading_name": "Sandbox Insurance", "abn": "51 824 753 556", "acn": "004 085 616",
        "licence_type": "ar", "ar_number": "1234567", "licensee_name": "Example Licensee Pty Ltd", "licensee_afsl": "123456",
        "address": "1 Test Street, Sydney NSW 2000", "phone": "02 9000 0000", "email": "info@sandbox.example.com.au",
        "website": "www.sandbox.example.com.au", "classes": "motor, property, liability", "commission_range": "0-25",
        "broker_fees": "A fee of $50 to $500 per policy", "fees_when": "when a policy is arranged or renewed", "other_benefits": "none",
        "staff_pay": "Salary only", "associations": "none", "pi_insurer": "Example PI Insurer Ltd",
        "pi_statement": "Our PI cover meets the requirements of s912B", "complaints_officer": "Jo Smith, 02 9000 0001, complaints@sandbox.example.com.au",
        "privacy_officer": "Sam Lee, privacy@sandbox.example.com.au", "overseas": "none"}


def test_abn_acn_afsl_validation():
    assert bd.abn_ok("51 824 753 556") and not bd.abn_ok("51 824 753 557") and not bd.abn_ok("123")
    assert bd.acn_ok("004 085 616") and not bd.acn_ok("004 085 617")
    for bad, field in [({"abn": "12 345 678 901"}, "abn"), ({"licence_type": "afsl", "afsl_number": "12"}, "afsl_number"),
                       ({"licence_type": "ar", "ar_number": "12x"}, "ar_number"), ({"email": "nope"}, "email"),
                       ({"commission_range": "30-10"}, "commission_range"), ({"licence_type": "xyz"}, "licence_type")]:
        with pytest.raises(bd.Invalid) as e:
            bd.validate(bad)
        assert field in e.value.errors
    assert bd.validate({"licence_type": "AFSL", "afsl_number": "123 456"})["afsl_number"] == "123456"


def test_every_placeholder_is_mapped():
    text = str(T.fsg()) + str(T.privacy_policy()) + T.collection_notice() + T.gaw() + T.afca()
    found = set(re.findall(r"\[PLACEHOLDER[^\]]*\]", text))
    unmapped = found - {T.P[k] for k in set(bd.SOURCES) | bd.AUTO}
    assert not unmapped, f"unmapped template placeholders: {unmapped}"
    for kind in lib.KINDS:  # with complete details, nothing is left blank
        secs, b = lib.compose(kind, bd.validate(GOOD), {}, 1)
        assert "[PLACEHOLDER" not in lib._plain(secs) and b["filled"] == b["total"] and not b["remaining"]


async def _tenant(db):
    t = await sandbox.ensure(db)
    return t["tenant_id"]


async def _by(db, tid):
    return {r["kind"]: r for r in await lib.listing(db, tid) if r.get("doc_id")}


@pytest.mark.asyncio
async def test_fill_progress_approve_live(app_client, mongo_db):
    _, http = app_client
    db, tid = mongo_db, await _tenant(mongo_db)
    by = await _by(db, tid)
    fsg = by["FSG"]
    assert fsg["blanks"]["filled"] == 0 and fsg["blanks"]["total"] == 14  # date + version are automatic, not counted
    with pytest.raises(lib.Bad, match="blanks still to fill"):
        await lib.approve(db, tid, fsg["doc_id"], reviewer_name="Rita", confirmed=True, by="p", role="principal")
    async with use_tenant(tid):
        safe = wrap(db)
        users = {u["email"]: u async for u in safe.users.find({})}
        roles = {r["user_id"]: r async for r in safe.user_brokerage_roles.find({})}

    async def cookie(email):
        u = users[email]
        s = await create_session_scoped(db, tid, user_id=u["user_id"], brokerage_id=roles[u["user_id"]]["brokerage_id"],
                                        active_role=roles[u["user_id"]]["role"], mfa_verified=True, ttl=timedelta(minutes=5))
        return {"Cookie": f"{sess.COOKIE_NAME}={s.session_id}"}
    pr, ae = await cookie(sandbox.STAFF[0]["email"]), await cookie(sandbox.STAFF[2]["email"])
    assert (await http.get("/api/o/compliance/business-details", headers=ae)).status_code == 403  # AE can't
    assert (await http.put("/api/o/compliance/business-details", headers=ae, json=GOOD)).status_code == 403
    bad = await http.put("/api/o/compliance/business-details", headers=pr, json={**GOOD, "abn": "12 345 678 901"})
    assert bad.status_code == 422 and "abn" in bad.json()["detail"]["errors"]
    half = await http.put("/api/o/compliance/business-details", headers=pr, json={"legal_name": GOOD["legal_name"], "abn": GOOD["abn"]})
    assert half.status_code == 200 and len(half.json()["regenerated"]) == 5
    fsg = (await _by(db, tid))["FSG"]
    assert fsg["version"] == 2 and 0 < fsg["blanks"]["filled"] < fsg["blanks"]["total"]
    ok = await http.put("/api/o/compliance/business-details", headers=pr, json=GOOD)
    assert ok.status_code == 200
    async with use_tenant(tid):
        ev = await safe.events.find_one({"type": "BusinessDetailsUpdated", "payload.version": 2})
        t = await safe.system("tenants").find_one({"tenant_id": tid})
    assert "licensee_name" in ev["payload"]["changed"] and "legal_name" not in ev["payload"]["changed"]
    assert t["legal_name"] == GOOD["legal_name"] and "Authorised Representative 1234567" in t["ar_number"]
    by = await _by(db, tid)
    assert all(not d["blanks"]["remaining"] for d in by.values())
    # wording edit = new version + diff
    fsg = by["FSG"]
    e = await http.put(f"/api/o/compliance/library/{fsg['doc_id']}/sections/6", headers=pr,
                       json={"body": "We have no associations. Ask us about any conflict of interest."})
    assert e.status_code == 200 and e.json()["version"] == fsg["version"] + 1
    vs = (await http.get(f"/api/o/compliance/library/{fsg['doc_id']}/versions", headers=pr)).json()["versions"]
    d = (await http.get(f"/api/o/compliance/library/{fsg['doc_id']}/diff?a={vs[-2]['version']}&b={vs[-1]['version']}", headers=pr)).json()
    assert any(x["op"] == "+" and "Ask us about any conflict" in x["line"] for x in d["lines"])
    # approve (no blanks) → live → clean render, checklist ticks
    a = await http.post(f"/api/o/compliance/library/{fsg['doc_id']}/approve", headers=pr, json={"reviewer_name": "Rita Reviewer", "confirmed": True})
    assert a.status_code == 200 and len(a.json()["approval"]["sha256"]) == 64
    assert (await http.post(f"/api/o/compliance/library/{fsg['doc_id']}/live", headers=pr, json={})).status_code == 200
    live_pdf, _, _ = await lib.download(db, tid, fsg["doc_id"], "pdf")
    draft_pdf, _, _ = await lib.download(db, tid, by["GAW"]["doc_id"], "pdf")
    live_docx, _, _ = await lib.download(db, tid, fsg["doc_id"], "docx")
    assert _txt(live_pdf).count(b"DRAFT") == 0 and b"DRAFT" in _txt(draft_pdf)
    assert b"DRAFT" not in _docx_text(live_docx).encode()
    from app.kernel import platform
    assert {i["key"]: i["done"] for i in await platform.checklist(db, tid)}["fsg"] is True
    # foreign tenant: someone else's doc id is 404
    import uuid as _uuid

    from app.kernel import trail_mongo
    otid = str(_uuid.uuid4())
    async with use_tenant(otid):
        await trail_mongo.create_tenant(wrap(db), f"ci-other-{otid[:6]}", "Other", tenant_id_override=otid)
    o = await create_session_scoped(db, otid, user_id="x", brokerage_id="b", active_role="principal", mfa_verified=True,
                                    ttl=timedelta(minutes=5))
    oc = {"Cookie": f"{sess.COOKIE_NAME}={o.session_id}"}
    assert (await http.get(f"/api/o/compliance/library/{fsg['doc_id']}/versions", headers=oc)).status_code == 404
    assert (await http.put(f"/api/o/compliance/library/{fsg['doc_id']}/sections/0", headers=oc, json={"body": "x" * 20})).status_code == 404


def _txt(pdf: bytes) -> bytes:
    import base64
    import zlib
    out = b""
    for m in re.finditer(rb"stream\r?\n(.*?)\s*endstream", pdf, re.S):
        raw = m.group(1).strip()
        try:
            out += zlib.decompress(base64.a85decode(raw[:-2] if raw.endswith(b"~>") else raw))
        except Exception:
            try:
                out += zlib.decompress(raw)
            except Exception:
                pass
    return out


def _docx_text(b: bytes) -> str:
    import io

    import docx
    d = docx.Document(io.BytesIO(b))
    return "\n".join([p.text for p in d.paragraphs] + [p.text for s in d.sections for p in s.header.paragraphs])
