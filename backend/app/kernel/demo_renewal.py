"""R2.1 demo: drive Kai's Tools-and-equipment renewal (…0150) through the real renewal path and stop at click-to-accept.

Harbourline demo tenant + dev mode only (callers check). Insurer quotes are MOCKED (mocked_allowed tenant); the bank line is
synthetic (described "KAI RENEWAL DEMO"). Re-runnable: any earlier renewal for the policy is reset via a ledgered RenewalDemoReset event.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from app.auth import sessions as sess
from app.auth.dispatch import create_session_scoped
from app.db.tenant_safe import wrap
from app.kernel import bankfeed
from app.kernel import renewals as rn
from app.kernel.tenant_ctx import use_tenant
from app.scripts import bank_synth
from app.seeds.seed_users import FICTIONAL_CLIENT_ID

POLICY = "00000000-0000-4000-8000-000000000150"  # Kai's Tools and equipment — independent of the …0104 endorsement demo
KAI, CASS = "owner@fictionalclient.example", "ae@harbourline.example"
MOCKED_INSURERS = ["Fictional Carrier A", "Fictional Carrier B"]
MANUAL = {"insurer": "Great Dividing Insurance (fictional)", "premium": "540.00", "excess": "500",
          "key_terms": ["Same cover as expiring", "Quote received by email from the insurer"]}
REC_TEXT = "Great Dividing Insurance is the lowest premium with the same cover as expiring."


class DemoFailed(RuntimeError):
    pass


async def reset_renewal(db, tid: str, by: str) -> list[str]:
    """Ledgered reset of every renewal on the demo policy: void its CRN, undo a bind, archive the projection row."""
    safe, now, done = wrap(db), datetime.now(timezone.utc), []
    async with use_tenant(tid):
        rows = [r async for r in safe.renewals.find({"policy_id": POLICY}, {"_id": 0})]
    for r in rows:
        await rn._event(db, tid, "RenewalDemoReset", {"renewal_id": r["renewal_id"], "envelope_id": r["envelope_id"],
                        "policy_id": POLICY, "stage": r["stage"], "payable_id": r.get("payable_id"),
                        "new_policy_id": r.get("new_policy_id"), "reason": "Demo: prepare client renewal at accept step"},
                        f"renewal_demo_reset:{r['renewal_id']}", by, "operator")
        async with use_tenant(tid):
            if r.get("payable_id"):
                await safe.payables.update_one({"payable_id": r["payable_id"]},
                                               {"$set": {"state": "void", "voided_at": now, "void_reason": "demo_reset"}})
            if r.get("new_policy_id"):
                await safe.policies.update_one({"policy_id": r["new_policy_id"]},
                                               {"$set": {"status": "cancelled", "cancelled_reason": "demo_reset"}})
            await safe.policies.update_one({"policy_id": POLICY}, {"$set": {"status": "managed"}})
            await safe.renewals_archive.insert_one({**r, "archived_at": now, "archived_reason": "demo_reset"})
            await safe.renewals.delete_one({"renewal_id": r["renewal_id"]})
        done.append(r["renewal_id"])
    return done


async def _cookies(db, tid: str) -> dict:
    safe = wrap(db)
    async with use_tenant(tid):
        kai = await safe.users.find_one({"email": KAI})
        cass = await safe.users.find_one({"email": CASS})
        role = await safe.user_brokerage_roles.find_one({"user_id": cass["user_id"], "role": "ae"})
    mk = lambda **kw: create_session_scoped(db, tid, mfa_verified=True, ttl=timedelta(minutes=10), **kw)  # noqa: E731
    k = await mk(user_id=kai["user_id"], client_id=str(FICTIONAL_CLIENT_ID), active_role="client_owner")
    c = await mk(user_id=cass["user_id"], brokerage_id=role["brokerage_id"], active_role="ae")
    return {"kai": {"Cookie": f"{sess.COOKIE_NAME}={k.session_id}"}, "cass": {"Cookie": f"{sess.COOKIE_NAME}={c.session_id}"},
            "cass_id": cass["user_id"]}


async def prepare(app, db, tid: str, by: str) -> dict:
    from app.scripts.r17_demo_disclosures import ensure_docs
    await ensure_docs(db, tid, MOCKED_INSURERS + [MANUAL["insurer"]])  # Harbourline fictional FSG/PDS/TMD
    reset = await reset_renewal(db, tid, by)
    ck = await _cookies(db, tid)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://demo.internal", timeout=60) as http:
        async def call(method, path, who, what, **kw):
            resp = await http.request(method, path, headers=ck[who], **kw)
            if resp.status_code != 200:
                raise DemoFailed(f"{what}: {resp.status_code} {resp.text[:200]}")
            return resp.json()

        rid = (await call("POST", "/api/o/renewals/start", "cass", "Start", json={"policy_id": POLICY}))["renewal"]["renewal_id"]
        o, c = f"/api/o/renewals/{rid}", f"/api/c/renewals/{rid}"
        await call("POST", f"{o}/request-info", "cass", "Request info", json={})
        await call("POST", f"{c}/info", "kai", "Client checklist", json={"answers": {
            "turnover": {"value": ""}, "claims": {"any": False}, "changes": {"text": ""}}})
        await call("POST", f"{o}/request-quotes", "cass", "Mocked quotes", json={"insurers": MOCKED_INSURERS})
        d = await call("POST", f"{o}/manual-quote", "cass", "Manual quote", json=MANUAL)
        manual = next(q["quote_id"] for q in d["renewal"]["quotes"] if q["insurer"] == MANUAL["insurer"])
        await call("POST", f"{o}/tmd-check", "cass", "TMD fit", json={"fit": "yes", "reason": "Small-business tools cover; inside the insurer's target market."})
        d = await call("POST", f"{o}/recommendation", "cass", "Recommendation", json={
            "quote_id": manual, "text": REC_TEXT, "reasons": ["Lowest premium", "Same limits as expiring"]})
        if d["renewal"]["recommendation"]["tier"] != "G":
            raise DemoFailed(f"Recommendation classified {d['renewal']['recommendation']['tier']}, expected G")
        await call("POST", f"{o}/send", "cass", "Send to client", json={})
        crn = (await call("POST", f"{c}/decide", "kai", "Client decides", json={"choice": manual}))["payment"]["crn"]
        await call("POST", f"{c}/paid", "kai", "Payment advice")
        line = [{"date": datetime.now(timezone.utc).date(), "amount": Decimal(MANUAL["premium"]),
                 "description": f"BPAY {crn} KAI RENEWAL DEMO", "balance": Decimal(MANUAL["premium"])}]
        await bankfeed.import_statement(db, tid, data=bank_synth.cba(line).encode(), filename=f"demo-renewal-{rid[:8]}.csv",
                                        user_id=ck["cass_id"], storage_path=None)  # not test-flagged: test lines only match test payables
        view = await call("GET", c, "kai", "Client view")
        acc = await call("GET", f"/api/c/acceptance/renewal/{rid}", "kai", "Acceptance document")
    if view["current"] != "paid" or acc["accepted"]:
        raise DemoFailed(f"Expected paid and not accepted; got {view['current']}, accepted={bool(acc['accepted'])}")
    chosen = next(x for x in view["options"] if x["quote_id"] == manual)
    return {"renewal_id": rid, "stage": view["current"], "crn": crn, "insurer": chosen["insurer"], "premium": chosen["premium"],
            "excess": chosen["excess"], "doc_sha256": acc["doc_sha256"], "accept_path": f"/c/renewal/{rid}", "reset": reset}
