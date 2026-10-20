"""R2.2: renewal comparison PDF — guard, one page, honesty labels, GAW, stable hash."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.auth import sessions as sess
from app.auth.dispatch import create_session_scoped
from app.db.tenant_safe import wrap
from app.kernel import comparison_pdf as cp
from app.kernel import demo_renewal
from app.kernel.tenant_ctx import use_tenant
from app.seeds.seed_users import FICTIONAL_CLIENT_ID

LONG = ["Great Dividing Mutual Insurance Company of Regional New South Wales and the Australian Capital Territory (fictional)",
        "Fictional Carrier A — Commercial Small-Business Tools, Plant and Portable Equipment Division (Underwritten in Sydney)",
        "Fictional Carrier B Specialist Trade Equipment & Contractors' Portable Property Insurance Pty Ltd (example)"]


def _text(pdf: bytes) -> bytes:
    """Inflate reportlab's compressed page streams so tests can look for printed words."""
    import re
    import zlib
    out = b""
    for m in re.finditer(rb"stream\r?\n(.*?)\s*endstream", pdf, re.S):
        try:
            out += zlib.decompress(m.group(1))
        except zlib.error:
            import base64
            raw = m.group(1).strip()
            raw = raw[:-2] if raw.endswith(b"~>") else raw
            try:
                out += zlib.decompress(base64.a85decode(raw))
            except Exception:
                pass
    return out


async def _setup(app, db, tid):
    out = await demo_renewal.prepare(app, db, tid, "demo-operator")
    ck = await demo_renewal._cookies(db, tid)
    return out["renewal_id"], ck


async def _get(http, rid, headers):
    return await http.get(f"/api/c/renewal/{rid}/comparison.pdf", headers=headers)


@pytest.mark.asyncio
async def test_comparison_pdf(seeded, app_client, mongo_db):
    app, http = app_client
    tid = seeded["tenant_id"]
    rid, ck = await _setup(app, mongo_db, tid)
    r1 = await _get(http, rid, ck["kai"])
    assert r1.status_code == 200 and r1.headers["content-type"] == "application/pdf" and r1.content[:5] == b"%PDF-"
    r2 = await _get(http, rid, ck["kai"])
    assert r2.headers["x-document-sha256"] == r1.headers["x-document-sha256"] and r2.content == r1.content  # stable hash
    assert cp.page_count(r1.content) == 1
    assert b"General advice warning" in _text(r1.content) and b"Example price" in _text(r1.content)
    safe = wrap(mongo_db)
    async with use_tenant(tid):
        r = await safe.renewals.find_one({"renewal_id": rid}, {"_id": 0})
        n = await safe.events.count_documents({"type": "ComparisonDocumentGenerated", "payload.renewal_id": rid})
        bea = await safe.users.find_one({"email": "bookkeeper@fictionalclient.example"})
    assert n == 1  # one event per (renewal, hash, user)
    c = await cp.content(mongo_db, tid, r)
    assert "General advice warning" in c["warning"] and any(o["demo"] for o in c["options"])
    assert sum(o["recommended"] for o in c["options"]) == 1
    assert not any(x in str(c) for x in (r["envelope_id"], rid, "CRN", "BSB"))  # safe to forward
    # the bookkeeper (any member who can view the client) may download
    b = await create_session_scoped(mongo_db, tid, user_id=bea["user_id"], client_id=str(FICTIONAL_CLIENT_ID),
                                    active_role="client_contact", mfa_verified=True, ttl=timedelta(minutes=5))
    assert (await _get(http, rid, {"Cookie": f"{sess.COOKIE_NAME}={b.session_id}"})).status_code == 200
    # foreign renewal (another client's) → 404
    foreign = {**r, "renewal_id": str(uuid.uuid4()), "client_id": str(uuid.uuid4()), "policy_id": str(uuid.uuid4()),
               "envelope_id": str(uuid.uuid4())}
    async with use_tenant(tid):
        await safe.renewals.insert_one(dict(foreign))
    assert (await _get(http, foreign["renewal_id"], ck["kai"])).status_code == 404
    # 3 options with long names and many terms still fit one page
    quotes = [{**q, "insurer": LONG[i], "key_terms": [f"Term {j}: " + "covers portable tools and equipment anywhere in Australia " * 2
                                                      for j in range(8)]} for i, q in enumerate(r["quotes"])]
    async with use_tenant(tid):
        await safe.renewals.update_one({"renewal_id": rid}, {"$set": {"quotes": quotes}})
        r = await safe.renewals.find_one({"renewal_id": rid}, {"_id": 0})
    pdf, _ = await cp.document(mongo_db, tid, r, "test-user")
    assert cp.page_count(pdf) == 1
    # manual_only: no example (mocked) prices at all
    async with use_tenant(tid):
        await safe.tenant_settings.update_one({}, {"$set": {"quoting_mode": "manual_only"}}, upsert=True)
    c = await cp.content(mongo_db, tid, r)
    assert c["options"] and not any(o["demo"] for o in c["options"])
    pdf, _ = await cp.document(mongo_db, tid, r, "test-user")
    assert b"Example price" not in _text(pdf) and b"General advice warning" in _text(pdf) and cp.page_count(pdf) == 1


def test_render_is_deterministic_and_escaped():
    c = {"brand": {"legal_name": "<b>Evil</b> & Co", "colour": "#0F1F3D", "logo": None}, "client": "A <script>", "policy_class": "Tools",
         "current_insurer": "X", "period": "01/01/2027 to 01/01/2028", "current": {"premium": "A$1.00", "excess": "A$0.00", "terms": []},
         "options": [{"insurer": "Y & <Z>", "recommended": True, "demo": False, "premium": "A$2.00", "change": "+A$1.00",
                      "excess": "A$0.00", "terms": ["<i>t</i>"], "differences": ["Same limits"]}],
         "recommendation": "r", "warning": "General advice warning: x", "docs": [], "contact": "c", "licence": "AFSL/AR: [not set]",
         "prepared": "Prepared by E via brokAId — summary only; the policy wording prevails."}
    a, pages = cp._render(c, "01/01/2027", "k" * 64, None, cp.FITS[0])
    b, _ = cp._render(c, "01/01/2027", "k" * 64, None, cp.FITS[0])
    assert a == b and pages == 1
