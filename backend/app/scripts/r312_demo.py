"""R3.12 — Harbourline demo: one policy in the renewal window with an open checklist, SAMPLE schedules for two policies
(current + previous term) and the PDS linked from the compliance library. Idempotent; ledgered; Harbourline only.
Usage: python -m app.scripts.r312_demo
"""
# tenant-safe: system-only (Harbourline resolved by slug; every write is tenant-scoped)
import asyncio
import hashlib
import io
import os
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

CLIENT = "00000000-0000-4000-8000-000000000001"
RENEW = "00000000-0000-4000-8000-000000000151"
SCHEDULES = ("00000000-0000-4000-8000-000000000101", RENEW)
MOTOR = "00000000-0000-4000-8000-000000000104"  # R3.13b: current SAMPLE schedule only
RENEW_IN_DAYS = 32


def sample_pdf(p: dict, client: str, start: datetime, end: datetime, label: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    w, h = A4
    c.setFillColorRGB(.75, .1, .1)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(40, h - 40, "SAMPLE: Harbourline demo — not a real policy document")
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, h - 80, f"Policy schedule · {p.get('product')}")
    c.setFont("Helvetica", 11)
    rows = [("Insured", client), ("Insurer", p.get("insurer_display") or ""), ("Term", label),
            ("Period", f"{start:%d %b %Y} to {end:%d %b %Y} (4pm local time)"),
            ("Premium (incl. GST and stamp duty)", f"A${float(p.get('premium_amount') or 0):,.2f}")]
    s = p.get("schedule") or {}
    for ct in s.get("cover_types") or []:
        amt = ct.get("limit_any_one") or ct.get("sum_insured")
        rows.append((ct.get("type") or "Cover", f"A${amt:,.0f}" if amt else "As per wording"))
    if (ex := (s.get("excess") or {}).get("any_one_occurrence")):
        rows.append(("Excess (any one occurrence)", f"A${ex:,.0f}"))
    if s.get("territorial_limits"):
        rows.append(("Situation / territory", s["territorial_limits"]))
    y = h - 120
    for k, v in rows:
        c.drawString(40, y, f"{k}:")
        c.drawString(250, y, str(v)[:60])
        y -= 20
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(40, 60, "SAMPLE: Harbourline demo. Read with the Product Disclosure Statement and policy wording.")
    c.showPage()
    c.save()
    return buf.getvalue()


async def _renewal(db, tid, now) -> dict:
    from app.kernel import renewals as rn
    if (r := await db.renewals.find_one({"tenant_id": tid, "policy_id": RENEW}, {"_id": 0})):
        return {"renewal_id": r["renewal_id"], "stage": rn.cur(r), "replayed": True}
    p = await db.policies.find_one({"tenant_id": tid, "policy_id": RENEW}, {"_id": 0})
    exp = (now + timedelta(days=RENEW_IN_DAYS)).replace(hour=6, minute=0, second=0, microsecond=0)
    await db.policies.update_one({"tenant_id": tid, "policy_id": RENEW},
                                 {"$set": {"expiry": exp, "demo_original_expiry": p["expiry"]}})
    await rn._event(db, tid, "DemoRenewalWindowSet", {"policy_id": RENEW, "client_id": CLIENT, "old_expiry": str(p["expiry"]),
                    "new_expiry": exp.isoformat(), "demo": True}, f"r312:renewal_window:{RENEW}", "r312", "operator")
    p["expiry"] = exp
    r = await rn.start(db, tid, p, by="r312", role="operator")
    r = await rn.request_info(db, tid, r, by="r312", role="operator")
    return {"renewal_id": r["renewal_id"], "stage": rn.cur(r)}


async def _schedules(db, tid, now) -> list:
    from app.kernel import object_store
    from app.kernel.renewals import _event
    client = (await db.clients.find_one({"tenant_id": tid, "client_id": CLIENT}) or {}).get("display_name") or "Client"
    out = []
    for cur in (True, False):  # label older SAMPLE rows with their slot once, so reruns stay idempotent after real uploads
        await db.policy_documents.update_many({"tenant_id": tid, "sample": True, "kind": "schedule", "current": cur, "term_slot": None},
                                              {"$set": {"term_slot": "current" if cur else "previous"}})
    for pid in (*SCHEDULES, MOTOR):
        p = await db.policies.find_one({"tenant_id": tid, "policy_id": pid}, {"_id": 0})
        start = p["inception"].replace(tzinfo=timezone.utc)
        end = p["expiry"].replace(tzinfo=timezone.utc) if p["expiry"].tzinfo is None else p["expiry"]
        terms = ((True, start, end),) if pid == MOTOR else ((True, start, end), (False, start - timedelta(days=365), start))
        for current, s, e in terms:
            label = f"{s:%-d %b %Y} – {e:%-d %b %Y}"
            q = {"tenant_id": tid, "policy_id": pid, "kind": "schedule", "sample": True, "term_slot": "current" if current else "previous"}
            if await db.policy_documents.find_one(q):
                await db.policy_documents.update_one(q, {"$set": {"term_label": label, "title": "Policy schedule"}})
                out.append({"policy_id": pid, "current": current, "replayed": True})
                continue
            pdf = sample_pdf(p, client, s, e, label)
            doc_id = str(uuid.uuid4())
            path = f"{object_store.APP_NAME}/tenants/{tid}/policy-docs/{pid}/{doc_id}.pdf"
            await object_store.put_object(path, pdf, "application/pdf")
            row = {"doc_id": doc_id, "tenant_id": tid, "client_id": CLIENT, "policy_id": pid, "kind": "schedule", "current": current,
                   "title": "Policy schedule", "term_label": label, "filename": f"schedule-{s:%Y}-SAMPLE.pdf",
                   "storage_path": path, "sha256": hashlib.sha256(pdf).hexdigest(), "sample": True, "demo": True,
                   "term_slot": "current" if current else "previous", "withdrawn_at": None,
                   "created_at": now}
            await db.policy_documents.insert_one(dict(row))
            await _event(db, tid, "PolicyDocumentAdded", {k: row[k] for k in ("doc_id", "policy_id", "client_id", "kind", "term_label",
                         "sha256", "sample")} | {"demo": True}, f"r312:schedule:{pid}:{current}", "r312", "operator")
            out.append({"policy_id": pid, "current": current, "doc_id": doc_id})
    return out


async def _pds(db, tid) -> list:
    from app.kernel import compliance_docs as cd
    out = []
    for pid in SCHEDULES:
        p = await db.policies.find_one({"tenant_id": tid, "policy_id": pid}, {"_id": 0})
        doc = await cd.active(db, tid, "PDS", p.get("insurer_display"), p.get("product"))
        if doc:
            d = await cd.deliver(db, tid, doc, client_id=CLIENT, user_id=None, channel="in_app", context=f"policy:{pid}")
            out.append({"policy_id": pid, "pds_delivery": d["delivery_id"]})
        else:
            out.append({"policy_id": pid, "pds": "none in library"})
    return out


async def _flag_qa(db, tid) -> int:
    from app.kernel.renewals import _event
    ids = [u["user_id"] async for u in db.users.find({"tenant_id": tid, "email": {"$regex": r"^qa-bk-[0-9a-f]+@harbourline\.example$"},
                                                      "test": {"$ne": True}})]
    if ids:
        await db.users.update_many({"tenant_id": tid, "user_id": {"$in": ids}}, {"$set": {"test": True}})
        await db.memberships.update_many({"tenant_id": tid, "user_id": {"$in": ids}}, {"$set": {"test": True}})
        await _event(db, tid, "DemoTestArtifactsFlagged", {"user_ids": ids, "reason": "earlier QA bookkeeper invites", "demo": True},
                     f"r312:flag_qa:{','.join(sorted(ids))}", "r312", "operator")
    return len(ids)


async def seed(db) -> dict:
    from app.kernel import devmode
    t = await db.tenants.find_one({"slug": "harbourline"})
    if not t or not devmode.is_demo_row(t):
        raise SystemExit("Harbourline demo tenant not found")
    tid, now = t["tenant_id"], datetime.now(timezone.utc)
    await db.policy_documents.create_index([("tenant_id", 1), ("doc_id", 1)], unique=True)
    return {"renewal": await _renewal(db, tid, now), "schedules": await _schedules(db, tid, now), "pds": await _pds(db, tid),
            "qa_flagged": await _flag_qa(db, tid)}


async def main() -> None:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    print(await seed(db))


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
