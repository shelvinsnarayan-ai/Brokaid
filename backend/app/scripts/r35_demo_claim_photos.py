"""R3.5 — two damage photos from the Harbourline demo client on CLM-2026-000013's claim thread (demo tenant only; idempotent; ledgered).

The message is placed before the unread broker reply (back-dated) and makes no new reply-by promise, so the client's unread
state and the thread's answered state are unchanged. Photos are generated (no people, no plates) and go through the same
strip/verify/store path as a real upload. No demo video (no small safe sample available).
Usage: python -m app.scripts.r35_demo_claim_photos
"""
# tenant-safe: system-only (Harbourline resolved by slug; every write is tenant-scoped)
import asyncio
import os
import uuid
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

REF = "CLM-2026-000013"
OWNER = "owner@fictionalclient.example"
TEXT = "Here are two photos of the rear bumper from this morning, in case the assessor needs them."
PHOTOS = (("rear-bumper-wide.jpg", "wide.jpg"), ("rear-bumper-close.jpg", "close.jpg"))
ASSETS = Path(__file__).parent / "assets" / "r35"


async def seed(db) -> dict:
    from app.kernel import claim_threads as ct, devmode, inbox
    from app.kernel.renewals import _event
    t = await db.tenants.find_one({"slug": "harbourline"})
    if not t or not devmode.is_demo_row(t):
        raise SystemExit("Harbourline demo tenant not found")
    tid = t["tenant_id"]
    claim = await db.claims.find_one({"tenant_id": tid, "claim_reference": REF}, {"_id": 0})
    owner = await db.users.find_one({"email": OWNER})
    msg_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"brokaid:r35-demo:{claim['claim_id']}"))
    if await ct.message_sent(db, tid, claim["claim_id"], msg_id):
        return {"claim_reference": REF, "msg_id": msg_id, "replayed": True}
    thread = await ct.thread_for(db, tid, claim["client_id"], claim["claim_id"], include_tests=True)
    replies = [m for m in thread["messages"] if m["who"] == "broker"]
    at = replies[-1]["at"] - timedelta(minutes=40)  # before the last (unread) broker reply
    rows = {r["doc"]["filename"]: r["doc"] for r in await ct.staged(db, tid, claim["claim_id"], msg_id, owner["user_id"])}
    docs = []
    for name, src in PHOTOS:
        docs.append(rows.get(name) or await ct.stage(db, tid, claim, src=str(ASSETS / src), content_type="image/jpeg", filename=name,
                                                    user_id=owner["user_id"], msg_id=msg_id))
    docs = await ct.attach(db, tid, claim, file_ids=[d["file_id"] for d in docs], msg_id=msg_id, user_id=owner["user_id"], role="client_owner")
    await ct.post_client(db, tid, user_id=owner["user_id"], role="client_owner", claim=claim, message=TEXT, msg_id=msg_id, received_at=at,
                         file_ids=[d["file_id"] for d in docs], attachment_summary=ct.summary(docs), promise=False)
    await _event(db, tid, "DemoClaimPhotosSeeded", {"claim_id": claim["claim_id"], "msg_id": msg_id, "file_ids": [d["file_id"] for d in docs]},
                 f"r35:claim_photos:{claim['claim_id']}", "r35", "operator")
    after = await ct.thread_for(db, tid, claim["client_id"], claim["claim_id"])
    return {"claim_reference": REF, "msg_id": msg_id, "files": len(docs), "answered": after["answered"],
            "order": [m["who"] for m in after["messages"]], "replayed": False, "basis": inbox.BASIS}


async def main() -> None:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    print(await seed(db))


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
