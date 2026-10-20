"""R2.5: the renewal comparison PDF by email — one per member, same bytes as the download, idempotent, gated."""
from __future__ import annotations

import base64
import hashlib
import re
from datetime import datetime, timezone

import pytest

from app.db.tenant_safe import wrap
from app.kernel import doc_email
from app.kernel import inbox
from app.kernel.tenant_ctx import use_tenant
from tests.test_r22_comparison_pdf import _get, _setup

ALLOW = ["*@resend.dev"]
DAY = datetime(2036, 10, 16, 10, 0, tzinfo=inbox.SYD).astimezone(timezone.utc)
NIGHT = datetime(2036, 10, 16, 23, 0, tzinfo=inbox.SYD).astimezone(timezone.utc)


class Sent(list):
    async def __call__(self, **kw):
        self.append(kw)
        return f"msg-{len(self)}"


async def _rows(db, tid, rid):
    async with use_tenant(tid):
        return [x async for x in wrap(db).doc_emails.find({"renewal_id": rid}, {"_id": 0}).sort("created_at", 1)]


async def _user(db, tid, email, **set_):
    safe = wrap(db)
    async with use_tenant(tid):
        u = await safe.users.find_one({"email": email})
        if set_:
            await safe.users.update_one({"user_id": u["user_id"]}, {"$set": set_})
    return u


@pytest.mark.asyncio
async def test_comparison_email_flow(seeded, app_client, mongo_db, monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    app, http = app_client
    tid = seeded["tenant_id"]
    rid, ck = await _setup(app, mongo_db, tid)  # "Send to client" queues the emails
    rows = await _rows(mongo_db, tid, rid)
    async with use_tenant(tid):
        r = await wrap(mongo_db).renewals.find_one({"renewal_id": rid}, {"_id": 0})
        members = {m["user_id"] async for m in wrap(mongo_db).memberships.find({"client_id": r["client_id"], "revoked_at": None})}
    assert len(rows) == len(members) >= 2 and {x["user_id"] for x in rows} == members  # owner + bookkeeper
    kai = await _user(mongo_db, tid, "owner@fictionalclient.example", email_override="delivered@resend.dev")
    bea = await _user(mongo_db, tid, "bookkeeper@fictionalclient.example")
    sent = Sent()
    out = await doc_email.run_once(mongo_db, DAY, send=sent, allow=ALLOW, ids=[x["email_id"] for x in rows])
    assert out == {"sent": 1, "skipped:example_address": 1}  # Bea's .example address is skipped
    m = sent[0]
    pdf = base64.b64decode(m["attachments"][0]["content"])
    dl = await _get(http, rid, ck["kai"])
    assert hashlib.sha256(pdf).hexdigest() == dl.headers["x-document-sha256"] and pdf == dl.content  # same bytes as the download
    assert m["attachments"][0]["filename"].startswith("Renewal-options-") and m["attachments"][0]["filename"].endswith("-16-10-2036.pdf")
    assert m["subject"].startswith("Your renewal options for ") and f"/c/renewal/{rid}" in m["text"]
    assert "General advice" in m["text"] or "general advice" in m["text"].lower()
    async with use_tenant(tid):
        pay = await wrap(mongo_db).payables.find_one({"ref_id": r["envelope_id"]}) or {}
    for body in (m["text"], m["html"], m["subject"]):
        assert "A$" not in body and not re.search(r"\$\s?\d", body)
        assert "CRN" not in body and (not pay.get("crn") or pay["crn"] not in body) and "BSB" not in body
    after = {x["user_id"]: x for x in await _rows(mongo_db, tid, rid)}
    assert after[kai["user_id"]]["channels_state"]["email"] == "sent" and after[kai["user_id"]]["message_id"] == "msg-1"
    assert after[bea["user_id"]]["channels_state"]["email"] == "skipped:example_address"
    async with use_tenant(tid):
        ev = await wrap(mongo_db).events.find_one({"type": "DocumentDelivered", "payload.channel": "email", "payload.renewal_id": rid})
        dd = await wrap(mongo_db).doc_deliveries.find_one({"kind": "RENEWAL_COMPARISON", "channel": "email"})
    assert ev["payload"]["sha256"] == dl.headers["x-document-sha256"] and ev["payload"]["message_id"] == "msg-1"
    assert dd and dd["message_id"] == "msg-1"

    # same content re-sent → no-op
    assert await doc_email.enqueue_comparison(mongo_db, tid, r, by=ck["cass_id"]) == []

    # options change → new hash → new email stating "updated options"
    changed = {**r, "product": f"{r['product']} (revised)"}
    new = await doc_email.enqueue_comparison(mongo_db, tid, changed, by=ck["cass_id"])
    assert len(new) == len(members)
    sent2 = Sent()
    await doc_email.run_once(mongo_db, DAY, send=sent2, allow=ALLOW, ids=new)
    assert len(sent2) == 1 and sent2[0]["subject"].startswith("Updated:") and "updated your renewal options" in sent2[0]["text"]

    # pref off → skipped:pref_off
    async with use_tenant(tid):
        await wrap(mongo_db).notification_prefs.update_one({"user_id": kai["user_id"]}, {"$set": {"documents_email": False}}, upsert=True)
    again = await doc_email.enqueue_comparison(mongo_db, tid, {**r, "product": "Pref test"}, by=ck["cass_id"])
    out = await doc_email.run_once(mongo_db, DAY, send=Sent(), allow=ALLOW, ids=again)
    assert out.get("skipped:pref_off") == 1
    async with use_tenant(tid):
        await wrap(mongo_db).notification_prefs.update_one({"user_id": kai["user_id"]}, {"$set": {"documents_email": True}})

    # quiet hours → held until 07:00 Sydney
    q = await doc_email.enqueue_comparison(mongo_db, tid, {**r, "product": "Quiet test"}, by=ck["cass_id"])
    s3 = Sent()
    out = await doc_email.run_once(mongo_db, NIGHT, send=s3, allow=ALLOW, ids=q)
    assert out.get("held") == 1 and not s3
    held = [x for x in await _rows(mongo_db, tid, rid) if x["email_id"] in q and x["channels_state"]["email"] == "held"][0]
    assert held["next_at"].replace(tzinfo=timezone.utc).astimezone(inbox.SYD).hour == 7

    # email disabled → skipped, in-app delivery untouched
    monkeypatch.setenv("EMAIL_ENABLED", "false")
    d = await doc_email.enqueue_comparison(mongo_db, tid, {**r, "product": "Off test"}, by=ck["cass_id"])
    assert (await doc_email.run_once(mongo_db, DAY, send=Sent(), allow=ALLOW, ids=d)) == {"skipped:email_disabled": len(members)}


@pytest.mark.asyncio
async def test_resend_is_ledgered_and_throttled(seeded, app_client, mongo_db):
    app, http = app_client
    tid = seeded["tenant_id"]
    rid, ck = await _setup(app, mongo_db, tid)
    before = len(await _rows(mongo_db, tid, rid))
    r1 = await http.post(f"/api/o/renewals/{rid}/resend-comparison", headers=ck["cass"], json={})
    assert r1.status_code == 200, r1.text
    assert len(r1.json()["comparison_emails"]) >= before
    assert len(await _rows(mongo_db, tid, rid)) == 2 * before  # a fresh email per member
    r2 = await http.post(f"/api/o/renewals/{rid}/resend-comparison", headers=ck["cass"], json={})
    assert r2.status_code == 429
    async with use_tenant(tid):
        assert await wrap(mongo_db).events.count_documents({"type": "ComparisonEmailResent", "payload.renewal_id": rid}) == 1
    # the client can't trigger it
    assert (await http.post(f"/api/o/renewals/{rid}/resend-comparison", headers=ck["kai"], json={})).status_code in (401, 403)


@pytest.mark.asyncio
async def test_documents_email_pref_defaults_on_for_clients(seeded, app_client, mongo_db):
    app, http = app_client
    tid = seeded["tenant_id"]
    _rid, ck = await _setup(app, mongo_db, tid)
    p = (await http.get("/api/notifications/prefs", headers=ck["kai"])).json()
    assert p["documents_email"] is True and p["email"] is False  # separate from alert emails
    p2 = (await http.put("/api/notifications/prefs", headers=ck["kai"], json={"email": False, "push": False, "documents_email": False})).json()
    assert p2["documents_email"] is False
    assert (await http.get("/api/notifications/prefs", headers=ck["cass"])).json()["documents_email"] is False
