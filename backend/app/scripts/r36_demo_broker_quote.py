"""R3.6 — a broker reply with a sample repair quote PDF on CLM-2026-000013 (Harbourline demo only; idempotent; ledgered).

Back-dated before the unread broker reply, so the client's unread state is unchanged and demo_refresh_unread keeps working.
The quote is generated here and clearly marked as a sample (no real business or ABN).
Usage: python -m app.scripts.r36_demo_broker_quote
"""
# tenant-safe: system-only (Harbourline resolved by slug; every write is tenant-scoped)
import asyncio
import io
import os
import tempfile
import uuid
from datetime import timedelta
from types import SimpleNamespace

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

REF = "CLM-2026-000013"
TEXT = ("Thanks for the photos. Northside Smash Repairs has sent their quote for the rear bumper, attached. "
        "We've passed it to the insurer with your photos.")
LINES = (("Rear bumper assembly (replace)", 1120.00), ("Paint and refinish, rear bumper", 680.00),
         ("Remove/refit sensors and lights", 327.27))


def quote_pdf() -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setTitle("Northside Smash Repairs — Quote Q-1042 (SAMPLE)")
    y = 790
    for text, size, bold in (("Northside Smash Repairs", 18, True), ("SAMPLE QUOTE — demo document, not a real business", 10, True),
                             ("ABN: sample", 10, False), ("Quote Q-1042", 14, True), ("Vehicle: Ford Ranger XLT · Rego XYZ-789", 10, False),
                             ("Damage: rear bumper (low-speed impact)", 10, False)):
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(56, y, text)
        y -= size + 10
    y -= 10
    c.setFont("Helvetica", 10)
    for item, amt in LINES:
        c.drawString(56, y, item)
        c.drawRightString(540, y, f"A${amt:,.2f}")
        y -= 18
    sub = sum(a for _, a in LINES)
    gst = round(sub * 0.10, 2)
    for label, amt, bold in (("Subtotal (ex GST)", sub, False), ("GST", gst, False), ("Total (incl GST)", 2340.00, True)):
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 11 if bold else 10)
        c.drawString(360, y, label)
        c.drawRightString(540, y, f"A${amt:,.2f}")
        y -= 18
    c.setFont("Helvetica", 8)
    c.drawString(56, 60, "Sample generated for the brokAId demo. Valid 30 days. Not an offer from a real repairer.")
    c.save()
    return buf.getvalue()


async def seed(db) -> dict:
    from app.kernel import claim_threads as ct, devmode, inbox
    from app.kernel.renewals import _event
    t = await db.tenants.find_one({"slug": "harbourline"})
    if not t or not devmode.is_demo_row(t):
        raise SystemExit("Harbourline demo tenant not found")
    tid = t["tenant_id"]
    claim = await db.claims.find_one({"tenant_id": tid, "claim_reference": REF}, {"_id": 0})
    thread_id = ct.thread_id_for(claim["claim_id"])
    draft = str(uuid.uuid5(uuid.NAMESPACE_URL, f"brokaid:r36-demo:{claim['claim_id']}"))
    key = f"r36-demo-quote-{claim['claim_id'][:8]}"
    if await db.thread_messages.find_one({"tenant_id": tid, "idempotency_key": key}):
        return {"claim_reference": REF, "replayed": True}
    ae = await db.users.find_one({"email": "ae@harbourline.example"})
    staff = SimpleNamespace(tenant_id=tid, user_id=ae["user_id"], active_role="ae", client_id=None)
    thread = await ct.thread_for(db, tid, claim["client_id"], claim["claim_id"], include_tests=True)
    replies = [m for m in thread["messages"] if m.get("reply_id")]
    at = replies[0]["at"] - timedelta(minutes=10)  # before both existing replies, so the last one stays the unread one
    rows = [r["doc"] for r in await ct.staged(db, tid, claim["claim_id"], draft, ae["user_id"])]
    if not rows:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(quote_pdf())
            f.flush()
            rows = [await ct.stage(db, tid, claim, src=f.name, content_type="application/pdf", filename="Northside-Smash-Repairs-Quote-Q-1042.pdf",
                                   user_id=ae["user_id"], msg_id=draft, role="ae", label="repair_quote")]
    ids = [d["file_id"] for d in rows]
    nkey = {"tenant_id": tid, "dedupe_key": f"broker_reply:{thread_id}"}
    before = await db.notifications.find_one(nkey)  # per-thread notification: keep it exactly as it was
    r = await inbox.reply(db, staff, thread_id, TEXT, key, file_ids=ids, attachment_summary=ct.summary(rows, broker=True), at=at)
    await ct.attach(db, tid, claim, file_ids=ids, msg_id=draft, user_id=ae["user_id"], role="ae", reply_id=r["reply_id"])
    if before:
        await db.notifications.replace_one({"_id": before["_id"]}, before)
    await db.proj_notifications.update_many({"tenant_id": tid, "client_id": claim["client_id"], "kind": "broker_reply", "ts": at},
                                            {"$set": {"read_at": at}})  # back-dated history, not a new alert
    await _event(db, tid, "DemoBrokerQuoteSeeded", {"claim_id": claim["claim_id"], "reply_id": r["reply_id"], "file_ids": ids},
                 f"r36:broker_quote:{claim['claim_id']}", "r36", "operator")
    from app.scripts.demo_refresh_unread import refresh
    return {"claim_reference": REF, "reply_id": r["reply_id"], "files": ids, "unread": await refresh(db)}


async def main() -> None:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    print(await seed(db))


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
