"""R3.13: every emitted event type has a plain sentence (or is deliberately generic); sentences never leak ids/JSON;
staff policy-document upload (PDF safety, RBAC, client visibility, withdraw)."""
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
    run(go)
