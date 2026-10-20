"""R2.0 — DRAFT compliance documents: draft → approved by a Principal (ledgered) → live; only live docs count for go-live."""
from __future__ import annotations

import io
import uuid
import zipfile

import pytest

from app.db.tenant_safe import wrap
from app.kernel import compliance_library as lib
from app.kernel import platform
from app.kernel.tenant_ctx import use_tenant


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch):
    blobs: dict[str, tuple[bytes, str]] = {}

    async def put(path, data, ct):
        blobs[path] = (data, ct)
        return {"path": path}

    async def get(path):
        return blobs[path]
    from app.kernel import object_store
    monkeypatch.setattr(object_store, "put_object", put)
    monkeypatch.setattr(object_store, "get_object", get)
    return blobs


async def _tenant(db):
    tid = str(uuid.uuid4())
    await db.tenants.insert_one({"tenant_id": tid, "slug": f"bm-{tid[:6]}", "display_name": "BurMac", "kind": "brokerage",  # tenant-safe: system-only
                                 "genesis_hash": "0" * 64})
    return tid


async def _events(db, tid, typ):
    async with use_tenant(tid):
        return [e async for e in wrap(db).events.find({"type": typ})]


@pytest.mark.asyncio
async def test_drafts_are_watermarked_placeholdered_and_never_count(mongo_db):
    db = mongo_db
    tid = await _tenant(db)
    made = await lib.generate_drafts(db, tid, by="u1", role="operator")
    assert sorted(m["kind"] for m in made) == ["AFCA", "COLLECTION_NOTICE", "FSG", "GAW", "PRIVACY_POLICY"]
    assert await lib.generate_drafts(db, tid, by="u1", role="operator") == []  # idempotent
    rows = await lib.listing(db, tid)
    by = {r["kind"]: r for r in rows}
    assert by["PDS"]["status"] == by["TMD"]["status"] == "obtain_from_insurer"  # never generated
    assert all(by[k]["status"] == "draft" and by[k]["placeholders"] > 0 for k in ("FSG", "PRIVACY_POLICY", "COLLECTION_NOTICE", "AFCA"))
    fsg = by["FSG"]["text"]
    for must in ("Who we are", "How we are paid", "General advice and personal advice", "Associations", "Your privacy",
                 "complaint", "www.afca.org.au", "1800 931 678", "info@afca.org.au", "GPO Box 3, Melbourne VIC 3001",
                 "Compensation arrangements", "912B"):
        assert must in fsg, must
    pp = by["PRIVACY_POLICY"]["text"]
    for app_no in range(1, 14):
        assert f"APP {app_no}" in pp, app_no
    assert "OAIC" in pp or "oaic" in pp
    pdf, ct, _ = await lib.download(db, tid, by["FSG"]["doc_id"], "pdf")
    docx_b, _, _ = await lib.download(db, tid, by["FSG"]["doc_id"], "docx")
    assert pdf[:4] == b"%PDF" and docx_b[:2] == b"PK"
    import docx
    d = docx.Document(io.BytesIO(docx_b))
    assert "DRAFT — for review by BurMac's licensee/compliance adviser. Not for client use." in d.sections[0].header.paragraphs[0].text
    z = zipfile.ZipFile(io.BytesIO(await lib.drafts_zip(db, tid)))
    assert len([n for n in z.namelist() if n.endswith((".docx", ".pdf"))]) == 10 and "README.txt" in z.namelist()
    items = {i["key"]: i["done"] for i in await platform.checklist(db, tid)}
    assert not any(items[k] for k in ("fsg", "gaw", "afca", "privacy", "collection"))
    assert len(await _events(db, tid, "ComplianceDraftCreated")) == 5


@pytest.mark.asyncio
async def test_principal_approval_then_live_is_the_only_path(mongo_db):
    db = mongo_db
    tid = await _tenant(db)
    await lib.generate_drafts(db, tid, by="u1", role="operator")
    by = {r["kind"]: r for r in await lib.listing(db, tid)}
    gaw, afca, fsg = by["GAW"], by["AFCA"], by["FSG"]
    with pytest.raises(lib.Bad) as e:  # only a Principal approves
        await lib.approve(db, tid, gaw["doc_id"], reviewer_name="R", confirmed=True, by="op", role="operator")
    assert e.value.status == 403
    with pytest.raises(lib.Bad):  # the tick is required
        await lib.approve(db, tid, gaw["doc_id"], reviewer_name="Rita Reviewer", confirmed=False, by="p", role="principal")
    with pytest.raises(lib.Bad, match="blanks? still to fill"):  # placeholders block approval
        await lib.approve(db, tid, afca["doc_id"], reviewer_name="Rita Reviewer", confirmed=True, by="p", role="principal")
    with pytest.raises(lib.Bad, match="blanks? still to fill"):  # R2.3: a generated FSG with blanks left can't be approved
        await lib.approve(db, tid, fsg["doc_id"], reviewer_name="Rita Reviewer", confirmed=True, by="p", role="principal")
    with pytest.raises(lib.Bad):  # a draft can't go live
        await lib.make_live(db, tid, gaw["doc_id"], by="p", role="principal")
    a = await lib.approve(db, tid, gaw["doc_id"], reviewer_name="Rita Reviewer", confirmed=True, by="p", role="principal")
    assert a["status"] == "approved" and a["approval"]["confirmation"] == lib.CONFIRM
    assert {i["key"]: i["done"] for i in await platform.checklist(db, tid)}["gaw"] is False  # approved isn't live
    live = await lib.make_live(db, tid, gaw["doc_id"], by="p", role="principal")
    assert live["status"] == "live"
    assert {i["key"]: i["done"] for i in await platform.checklist(db, tid)}["gaw"] is True
    async with use_tenant(tid):
        st = await wrap(db).tenant_settings.find_one({})
    assert st["gaw_text"] == gaw["text"]
    ev = (await _events(db, tid, "ComplianceDocumentApproved"))[0]["payload"]
    assert ev["reviewer_name"] == "Rita Reviewer" and ev["confirmation"] == lib.CONFIRM and len(ev["sha256"]) == 64
    assert len(await _events(db, tid, "ComplianceDocumentLive")) == 1
    # FSG: upload the completed PDF (magic-byte checked), approve, make live → the active disclosure FSG + checklist
    with pytest.raises(lib.Bad):
        await lib.upload_final(db, tid, fsg["doc_id"], b"not a pdf", by="p", role="principal")
    await lib.upload_final(db, tid, fsg["doc_id"], b"%PDF-1.4\nfinal BurMac FSG", by="p", role="principal")
    await lib.approve(db, tid, fsg["doc_id"], reviewer_name="Rita Reviewer", confirmed=True, by="p", role="principal")
    await lib.make_live(db, tid, fsg["doc_id"], by="op", role="operator")
    assert {i["key"]: i["done"] for i in await platform.checklist(db, tid)}["fsg"] is True
    # filled-in text can be approved
    filled = afca["text"].split("\n", 1)[1].replace("[PLACEHOLDER: complaints officer name, phone, email, postal address]", "Jo Smith, 02 9000 0000")
    await lib.edit_section(db, tid, afca["doc_id"], 0, filled, by="p", role="principal")
    assert (await lib.approve(db, tid, afca["doc_id"], reviewer_name="Rita", confirmed=True, by="p", role="principal"))["status"] == "approved"


@pytest.mark.asyncio
async def test_direct_fsg_upload_is_refused(app_client, mongo_db):
    from datetime import timedelta

    from app.auth.dispatch import create_session_scoped
    from app.auth.sessions import COOKIE_NAME
    _, http = app_client
    tid = await _tenant(mongo_db)
    s = await create_session_scoped(mongo_db, tid, user_id="p1", brokerage_id="b", active_role="principal", mfa_verified=True,
                                    ttl=timedelta(minutes=5))
    r = await http.post("/api/o/compliance/documents", headers={"Cookie": f"{COOKIE_NAME}={s.session_id}"},
                        data={"kind": "FSG", "version": "1", "effective_date": "2026-10-01"},
                        files={"file": ("fsg.pdf", b"%PDF-1.4 x", "application/pdf")})
    assert r.status_code == 409 and "library" in r.json()["detail"]
