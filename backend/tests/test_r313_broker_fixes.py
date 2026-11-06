"""R3.13: every emitted event type has a plain sentence (or is deliberately generic); sentences never leak ids/JSON;
staff policy-document upload (PDF safety, RBAC, client visibility, withdraw)."""
import json
import re
from datetime import timedelta
from pathlib import Path

from app.auth.dispatch import create_session_scoped
from app.auth.sessions import COOKIE_NAME
from app.db.tenant_safe import wrap
from app.kernel import event_text as et
from app.kernel.tenant_ctx import use_tenant
from tests.test_r12_auth import _events, _tid, run

APP = Path(__file__).resolve().parents[1] / "app"
EMIT = re.compile(r"(?:append_via_dispatch|ledger|_event|_ledger|emit|record_event|_append|_emit|append_event|_log_event|_record)"
                  r"\([^\"]{0,80}\"([A-Z][A-Za-z0-9]+)\"")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
CID = "00000000-0000-4000-8000-000000000001"
PID = "00000000-0000-4000-8000-000000000104"


def _emitted() -> set[str]:
    out = set()
    for f in APP.rglob("*.py"):
        out |= set(EMIT.findall(f.read_text(errors="ignore")))
    out |= set(re.findall(r"\"type\": \"([A-Z][A-Za-z0-9]+)\"", "\n".join(f.read_text(errors="ignore") for f in APP.rglob("*.py"))))
    return out


def test_every_emitted_event_type_has_a_template_or_is_generic():
    missing = sorted(t for t in _emitted() if t not in et.T and not et.is_generic(t))
    assert not missing, f"add a sentence to app/kernel/event_text.py (or mark generic): {missing}"


def test_sentences_are_plain():
    p = {"claim_id": "5fb96b86-9a10-41d7-ae72-51b823cb1eb3", "client_id": CID, "kind": "schedule", "to": "info_requested",
         "class_key": "endorsement.add_vehicle", "amount": 85, "nested": {"x": 1}}
    for t in et.T:
        d = et.describe(t, p, "Cass")
        assert d["text"] and not UUID.search(d["text"]) and "{" not in d["text"] and "None" not in d["text"], (t, d["text"])
    assert et.describe("CarrierQuoted", {"amount": 85}, None)["text"] == "Insurer quoted A$85.00"
    assert et.describe("Approval", {"class_key": "endorsement.add_vehicle", "decision": "approved"}, "Cass")["text"] == \
        "Cass approved the endorsement add vehicle"
    assert et.describe("Unheard", {}, None) == {"text": "System update recorded", "link": None}
    assert et.describe("ClaimStageChanged", p)["link"] == "/o/claims/5fb96b86-9a10-41d7-ae72-51b823cb1eb3"


JS_PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >>\nendobj\ntrailer<<>>\n%%EOF"
OK_PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >>\nendobj\ntrailer<<>>\n%%EOF"


def test_staff_upload_withdraw_and_rbac():
    async def go(db, c, app):
        tid = await _tid(db)
        async with use_tenant(tid):
            ae = await wrap(db).users.find_one({"email": "ae@harbourline.example"})
            op = await wrap(db).users.find_one({"email": "operator@harbourline.example"})
            owner = await wrap(db).users.find_one({"email": "owner@fictionalclient.example"})

        async def jar(uid, role, cid=None):
            s = await create_session_scoped(db, tid, user_id=uid, client_id=cid, active_role=role, mfa_verified=True, ttl=timedelta(minutes=30))
            return {COOKIE_NAME: s.session_id}
        aej, opj, cj = await jar(ae["user_id"], "ae"), await jar(op["user_id"], "operator"), await jar(owner["user_id"], "client_owner", CID)
        f = lambda b: {"file": ("s.pdf", b, "application/pdf")}  # noqa: E731
        form = {"kind": "schedule", "term_label": "1 Jul 2026 – 1 Jul 2027", "current": "true"}
        assert (await c.post(f"/api/o/policies/{PID}/documents", cookies=opj, files=f(OK_PDF), data=form)).status_code == 403
        r = await c.post(f"/api/o/policies/{PID}/documents", cookies=aej, files=f(JS_PDF), data=form)
        assert r.status_code == 422
        assert (await c.post(f"/api/o/policies/{PID}/documents", cookies=aej, files={"file": ("x.txt", b"hi", "text/plain")},
                             data=form)).status_code == 415
        r = await c.post(f"/api/o/policies/{PID}/documents", cookies=aej, files=f(OK_PDF), data=form)
        assert r.status_code == 200, r.text
        doc = r.json()["document"]["doc_id"]
        lib = (await c.get("/api/c/documents/library", cookies=cj)).json()
        motor = next(p for p in lib["policies"] if p["policy_id"] == PID)
        assert [x["id"] for x in next(g for g in motor["groups"] if g["key"] == "schedule")["items"]] == [doc]
        assert (await c.get(f"/api/c/documents/file/{doc}", cookies=cj)).content == OK_PDF
        ev = await _events(db, tid, "PolicyDocumentAdded")
        assert ev and ev[-1]["payload"]["doc_id"] == doc
        # replace keeps the old one (withdrawn) and the new one is current
        r = await c.post(f"/api/o/policies/{PID}/documents", cookies=aej, files=f(OK_PDF + b"\n"), data={**form, "replaces": doc})
        new = r.json()["document"]["doc_id"]
        assert (await c.get(f"/api/c/documents/file/{doc}", cookies=cj)).status_code == 404
        assert (await _events(db, tid, "PolicyDocumentReplaced"))[-1]["payload"]["doc_id"] == doc
        assert (await c.post(f"/api/o/policy-documents/{new}/withdraw", cookies=opj, json={"reason": "wrong file"})).status_code == 403
        assert (await c.post(f"/api/o/policy-documents/{new}/withdraw", cookies=aej, json={"reason": "wrong file"})).status_code == 200
        assert new not in (await c.get("/api/c/documents/library", cookies=cj)).text
        async with use_tenant(tid):
            assert await wrap(db).policy_documents.count_documents({"policy_id": PID}) == 2  # never deleted
        staff = (await c.get(f"/api/o/clients/{CID}/policy-documents", cookies=opj)).json()
        assert staff["can_edit"] is False and any(d["withdrawn"] for p in staff["policies"] for d in p["documents"])
        # withdrawing a new current schedule puts the one it replaced as current back
        a = (await c.post(f"/api/o/policies/{PID}/documents", cookies=aej, files=f(OK_PDF), data=form)).json()["document"]["doc_id"]
        b = (await c.post(f"/api/o/policies/{PID}/documents", cookies=aej, files=f(OK_PDF), data=form)).json()["document"]["doc_id"]
        async with use_tenant(tid):
            assert not (await wrap(db).policy_documents.find_one({"doc_id": a}))["current"]
        sub = {x["id"]: x["subtitle"] for p in (await c.get("/api/c/documents/library", cookies=cj)).json()["policies"]
               for g in p["groups"] for x in g["items"]}
        assert "previous term" in sub[a] and "current term" not in sub[a] and "current term" in sub[b], sub
        assert (await c.post(f"/api/o/policy-documents/{b}/withdraw", cookies=aej, json={"reason": "wrong term"})).status_code == 200
        async with use_tenant(tid):
            assert (await wrap(db).policy_documents.find_one({"doc_id": a}))["current"] is True
        # alerts: older notifications get client_name backfilled from their source event
        async with use_tenant(tid):
            await wrap(db).notifications.insert_one({"notification_id": "n-old", "user_id": ae["user_id"], "kind": "broker_review",
                                                     "title": "t", "body": "b", "link": f"/o/clients/{CID}", "read_at": None,
                                                     "created_at": tok_time(), "updated_at": tok_time(), "test": False})
            name = (await wrap(db).clients.find_one({"client_id": CID}))["display_name"]
        al = (await c.get("/api/notifications", cookies=aej)).json()["notifications"]
        assert next(n for n in al if n["id"] == "n-old")["client_name"] == name
    run(go)


def tok_time():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


DENIED = {"payload", "user_id", "membership_id", "invite_id", "token_id", "event_id", "source_event_id", "proposal_ref"}


def _bad_keys(obj, path="") -> list[str]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if kl == "aes":  # explicitly allowed: AE picker options {user_id, name}
                continue
            if kl in DENIED or "sha256" in kl or kl.endswith("_hash") or UUID.fullmatch(str(k)) or re.fullmatch(r"[0-9a-f]{40,64}", kl):
                out.append(f"{path}.{k}")
            out += _bad_keys(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, x in enumerate(obj):
            out += _bad_keys(x, f"{path}[{i}]")
    return out


def test_staff_event_and_evidence_endpoints_redact_for_ae_and_principal():
    """R3.13b: AE/Principal JSON has no raw payload, hashes, person/credential ids; Operator still gets raw details."""
    async def go(db, c, app):
        from app.kernel import renewals as rn
        tid = await _tid(db)
        async with use_tenant(tid):
            ae = await wrap(db).users.find_one({"email": "ae@harbourline.example"})
            op = await wrap(db).users.find_one({"email": "operator@harbourline.example"})
            pr = await wrap(db).users.find_one({"email": "qa.principal@harbourline.example"})
        await rn._event(db, tid, "ClientMemberInvited", {"client_id": CID, "invite_id": "11111111-2222-4333-8444-555555555555",
                        "membership_id": "66666666-2222-4333-8444-555555555555", "file_sha256": "ab" * 32, "note": "secret note"},
                        "r313b:test:invite", ae["user_id"], "ae")

        async def jar(uid, role):
            s = await create_session_scoped(db, tid, user_id=uid, active_role=role, mfa_verified=True, ttl=timedelta(minutes=30))
            return {COOKIE_NAME: s.session_id}
        aej, opj = await jar(ae["user_id"], "ae"), await jar(op["user_id"], "operator")
        prj = await jar(pr["user_id"], "principal") if pr else None

        async def urls(j):
            u = [f"/api/o/client/{CID}/timeline", "/api/o/queue", "/api/o/claims", "/api/o/inbox", "/api/o/renewals",
                 "/api/o/renewal-review", "/api/notifications"]
            q = (await c.get("/api/o/queue", cookies=j)).json().get("cards") or []
            u += [f"/api/o/queue/{x['envelope_id']}" for x in q[:3] if x.get("envelope_id")]
            cl = (await c.get("/api/o/claims", cookies=j)).json().get("claims") or []
            for x in cl[:2]:
                u += [f"/api/o/claims/{x['claim_id']}", f"/api/o/claims/{x['claim_id']}/thread"]
            th = (await c.get("/api/o/inbox", cookies=j)).json().get("threads") or []
            u += [f"/api/o/inbox/{x['thread_id']}" for x in th[:3] if x.get("thread_id")]
            rw = (await c.get("/api/o/renewals", cookies=j)).json().get("renewals") or []
            u += [f"/api/o/renewals/{x['renewal_id']}" for x in rw[:2] if x.get("renewal_id")]
            ks = (await c.get("/api/o/kpi/summary", cookies=j)).json()
            kids = [k.get("id") or k.get("kpi_id") for k in (ks.get("kpis") if isinstance(ks, dict) else ks) or [] if isinstance(k, dict)]
            u += [f"/api/o/kpi/{k}/events" for k in kids[:3] if k]
            return u

        for j, who in ((aej, "ae"), (prj, "principal")):
            if not j:
                continue
            checked = 0
            for u in await urls(j):
                r = await c.get(u, cookies=j)
                if r.status_code != 200:
                    continue
                checked += 1
                body = r.json()
                bad = _bad_keys(body)
                assert not bad, (who, u, bad[:5])
                text = json.dumps({k: v for k, v in body.items() if k != "aes"} if isinstance(body, dict) else body)
                assert ae["user_id"] not in text and "ab" * 32 not in text and "secret note" not in text, (who, u)
            assert checked >= 6, (who, checked)
        tl = (await c.get(f"/api/o/client/{CID}/timeline", cookies=aej)).json()
        rows = tl.get("events") if isinstance(tl, dict) else tl
        assert any("invite" in (e.get("text") or "").lower() for e in rows), rows[:3]
        assert all(set(e) <= {"ts", "text", "link", "who"} for e in rows)
        opt = (await c.get(f"/api/o/client/{CID}/timeline", cookies=opj)).text
        assert "secret note" in opt and "ab" * 32 in opt and '"payload"' in opt
        if prj:
            ev = await c.get("/api/o/compliance/ai-eval", cookies=prj)
            assert ev.status_code == 200 and not _bad_keys(ev.json()), _bad_keys(ev.json())[:3]
    run(go)


def _strings(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(o, list):
        for x in o:
            yield from _strings(x)
    elif isinstance(o, str):
        yield o


def test_kpi_summary_and_drill_name_people_not_ids_and_hide_qa_notes():
    """R3.13c: KPI summary/drill (incl. kpi 10 approvals load) carry names, not user ids — as values or dict keys;
    QA/TEST_/cleanup notes stay off AE/Principal timelines. Operator keeps raw values."""
    async def go(db, c, app):
        from app.kernel import renewals as rn
        tid = await _tid(db)
        async with use_tenant(tid):
            ae = await wrap(db).users.find_one({"email": "ae@harbourline.example"})
            op = await wrap(db).users.find_one({"email": "operator@harbourline.example"})
            pr = await wrap(db).users.find_one({"email": "qa.principal@harbourline.example"})
            ids = {u["user_id"] async for u in wrap(db).users.find({}, {"user_id": 1})}
        for i, uid in enumerate((ae["user_id"], "accounts-a", "accounts-b")):
            await rn._event(db, tid, "Approval", {"user_id": uid, "role": "ae" if i == 0 else "accounts", "decision": "approved",
                            "envelope_id": f"00000000-0000-4000-8000-00000000e{i:03d}"}, f"r313c:test:appr:{i}", uid, "ae")
        await rn._event(db, tid, "ClientQuestionAsked", {"client_id": CID, "message": "TEST_ cleanup after QA"}, "r313c:test:qa", "system")
        ids |= {"accounts-a", "accounts-b"}

        async def jar(uid, role):
            s = await create_session_scoped(db, tid, user_id=uid, active_role=role, mfa_verified=True, ttl=timedelta(minutes=30))
            return {COOKIE_NAME: s.session_id}
        for j in (await jar(ae["user_id"], "ae"), await jar((pr or op)["user_id"], "principal")):
            summ = (await c.get("/api/o/kpi/summary", cookies=j)).json()
            assert not _bad_keys(summ), _bad_keys(summ)[:5]
            assert not (set(_strings(summ)) & ids), sorted(set(_strings(summ)) & ids)[:5]
            k10 = next(k for k in summ["kpis"] if k["kpi_id"] == 10)["meta"]
            assert {"Accounts A", "Accounts B"} <= set(k10["per_user_count"]), k10["per_user_count"]
            assert all(e.get("who") for e in k10["events"])
            dr = (await c.get("/api/o/kpi/10/events?limit=500", cookies=j)).json()
            assert not _bad_keys(dr) and not (set(_strings(dr)) & ids)
            assert any(e["who"] == "Accounts A" and "approved" in e["text"] for e in dr["events"]), dr["events"][:3]
            tl = (await c.get(f"/api/o/client/{CID}/timeline?limit=200", cookies=j)).text
            assert "TEST_" not in tl and "QA" not in tl
        opj = await jar(op["user_id"], "operator")
        raw = (await c.get("/api/o/kpi/summary", cookies=opj)).json()
        assert "accounts-a" in next(k for k in raw["kpis"] if k["kpi_id"] == 10)["meta"]["per_user_count"]
    run(go)
