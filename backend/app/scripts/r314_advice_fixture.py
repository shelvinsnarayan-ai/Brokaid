"""R3.14 evidence fixture: one pending Principal advice approval WITH a file on the CLM-2026-000023 claim thread, created as the AE
through the real routes (upload draft attachment → request advice approval), flagged test. `--remove` withdraws it as the author
(held file goes back to the AE's drafts, nothing reaches the client) and flags its events test. Harbourline only; idempotent.
Usage: python -m app.scripts.r314_advice_fixture [--remove]
"""
# tenant-safe: system-only (Harbourline resolved by slug; every read/write is tenant-scoped)
import asyncio
import io
import os
import sys
import uuid
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

CLAIM = "65e0039b-ac3d-4e39-bc06-dc91f04199cc"
THREAD = f"claim-{CLAIM}"
API = "http://localhost:8001"
BODY = ("TEST_ review evidence (R3.14): based on the assessor's report attached, we recommend you accept the insurer's offer. "
        "This is a test approval for screenshots and will be withdrawn.")
WHY = "R3.14 review evidence fixture (TEST_)"


def _pdf() -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    b = io.BytesIO()
    c = canvas.Canvas(b, pagesize=A4)
    c.drawString(72, 780, "SAMPLE — TEST_ assessor report (R3.14 review evidence). Not a real document.")
    c.save()
    return b.getvalue()


async def _session(email: str) -> dict:
    async with httpx.AsyncClient(base_url=API) as c:
        r = await c.post("/api/auth/dev/impersonate", json={"email": email})
        r.raise_for_status()
        sid = r.cookies.get("__Host-brokaid_session") or next(
            h.split(";", 1)[0].split("=", 1)[1] for h in r.headers.get_list("set-cookie") if h.startswith("__Host-brokaid_session="))
    return {"Cookie": f"__Host-brokaid_session={sid}"}


async def _flag(db, tid, kind, ref):
    await db.test_flags.update_one({"tenant_id": tid, "kind": kind, "ref": ref},
                                   {"$setOnInsert": {"reason": WHY, "flagged_at": datetime.now(timezone.utc)}}, upsert=True)


async def create(db, tid) -> dict:
    old = await db.reply_advice_approvals.find_one({"tenant_id": tid, "thread_id": THREAD, "status": "pending", "test": True})
    if old:
        return {"approval_id": old["approval_id"], "replayed": True}
    h = await _session("ae@harbourline.example")
    draft = str(uuid.uuid4())
    async with httpx.AsyncClient(base_url=API, headers=h, timeout=60) as c:
        async for o in db.claim_uploads.find({"tenant_id": tid, "claim_id": CLAIM, "status": "staged", "held_by": None,
                                              "doc.filename": "TEST_assessor-report-SAMPLE.pdf"}, {"file_id": 1}):
            await c.delete(f"/api/o/claims/{CLAIM}/thread/attachments/{o['file_id']}")  # an earlier half-finished run
        up = await c.post(f"/api/o/claims/{CLAIM}/thread/attachments", data={"msg_id": draft, "label": "assessor_report"},
                          files={"file": ("TEST_assessor-report-SAMPLE.pdf", _pdf(), "application/pdf")})
        up.raise_for_status()
        f = up.json()["file"]
        fid = f.get("file_id") or f["id"]
        r = await c.post(f"/api/o/inbox/{THREAD}/advice-approval",
                         json={"body": BODY, "draft_id": draft, "file_ids": [fid], "labels": {fid: "assessor_report"}, "quotes": {}})
        r.raise_for_status()
    a = r.json()
    await db.reply_advice_approvals.update_one({"tenant_id": tid, "approval_id": a["approval_id"]}, {"$set": {"test": True}})
    await db.claim_uploads.update_one({"tenant_id": tid, "file_id": fid}, {"$set": {"test": True}})
    await _flag(db, tid, "approval", a["approval_id"])
    return {"approval_id": a["approval_id"], "file_id": fid}


async def remove(db, tid) -> dict:
    from app.kernel import renewals as rn
    out = []
    async for a in db.reply_advice_approvals.find({"tenant_id": tid, "thread_id": THREAD, "test": True}):
        if a["status"] == "pending":
            h = await _session("ae@harbourline.example")
            async with httpx.AsyncClient(base_url=API, headers=h, timeout=60) as c:
                (await c.post(f"/api/o/inbox/{THREAD}/advice-approval/{a['approval_id']}/withdraw")).raise_for_status()
        evs = [e["event_id"] async for e in db.events.find({"tenant_id": tid, "payload.approval_id": a["approval_id"]}, {"event_id": 1})]
        for e in evs:
            await _flag(db, tid, "event", e)
        await db.notifications.update_many({"tenant_id": tid, "dedupe_key": f"advice_approval:{a['approval_id']}"},
                                           {"$set": {"test": True, "read_at": datetime.now(timezone.utc)}})
        out.append({"approval_id": a["approval_id"], "events_flagged": len(evs)})
    if out:
        await rn._event(db, tid, "DemoTestDataFlagged", {"kind": "r314_advice_fixture", "refs": [o["approval_id"] for o in out], "reason": WHY},
                        f"r314:fixture:remove:{','.join(o['approval_id'] for o in out)}", "r314", "operator")
    return {"removed": out}


async def main():
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    tid = (await db.tenants.find_one({"slug": "harbourline"}, {"tenant_id": 1}))["tenant_id"]
    print(await (remove(db, tid) if "--remove" in sys.argv else create(db, tid)))


if __name__ == "__main__":
    asyncio.run(main())
