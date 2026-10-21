"""R3.4 — a short claim thread for the Harbourline demo client's open claim (demo tenant only; idempotent; ledgered).

Client message → broker reply (keeps the reply-by promise) → second broker reply left unread for the demo client.
If the demo client has no open claim, lodges one realistic open motor claim on an existing vehicle.
Usage: python -m app.scripts.r34_demo_claim_thread
"""
# tenant-safe: system-only (Harbourline resolved by slug; every write is tenant-scoped)
import asyncio
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

CID = "00000000-0000-4000-8000-000000000001"
OWNER = "owner@fictionalclient.example"
MSG = "Hi — the other driver's insurer called me today. Should I give them my details, or leave it with you?"
R1 = ("Thanks for letting us know. We have passed our contact details to the other driver's insurer, so any further calls "
      "will come to us. The claim has now been sent to the insurer's assessor.")
R2 = ("Update: the assessor has asked for two more photos of the rear bumper. They can be added on the claim page under uploads. "
      "The excess for this policy is shown on the claim page too.")


async def _open_claim(db, tid):
    from app.kernel import claims as ck
    c = await db.claims.find_one({"tenant_id": tid, "client_id": CID, "test": {"$ne": True}, "stage": {"$nin": ["closed", "withdrawn"]},
                                  "claim_class": "motor"}, sort=[("lodged_at", 1)])
    if c:
        return c
    pol = await db.policies.find_one({"tenant_id": tid, "client_id": CID, "status": "managed", "schedule.vehicles.0": {"$exists": True}})
    v = pol["schedule"]["vehicles"][0]
    body = {"claim_class": "motor", "subtype": "accident", "incident_date": (datetime.now(timezone.utc) - timedelta(days=3)).date().isoformat(),
            "where": "Corner of Bay St and Marine Pde", "what": "Rear-ended at low speed while stopped at lights.", "rego": v["rego"],
            "parties": [], "injuries": False, "safe": True}
    return await ck.lodge(db, tid, client_id=CID, policy=pol, body=body, by="r34-seed", role="client", channel="in_app")


async def seed(db) -> dict:
    from app.kernel import claim_threads as ct, devmode, inbox
    from app.kernel.renewals import _event
    t = await db.tenants.find_one({"slug": "harbourline"})
    if not t or not devmode.is_demo_row(t):
        raise SystemExit("Harbourline demo tenant not found")
    tid = t["tenant_id"]
    claim = await _open_claim(db, tid)
    owner = await db.users.find_one({"email": OWNER})
    ae = await db.users.find_one({"user_id": claim.get("assigned_ae")}) if claim.get("assigned_ae") else None
    if not ae:
        m = await db.memberships.find_one({"tenant_id": tid, "role": {"$in": ["ae", "principal"]}, "revoked_at": None})
        ae = await db.users.find_one({"user_id": m["user_id"]})
    staff = SimpleNamespace(tenant_id=tid, user_id=ae["user_id"], active_role="ae", client_id=None)
    await ct.post_client(db, tid, user_id=owner["user_id"] if owner else "r34-seed", role="client", claim=claim, message=MSG,
                         msg_id="r34-demo-msg-1", received_at=datetime.now(timezone.utc) - timedelta(hours=20))
    thread_id = ct.thread_id_for(claim["claim_id"])
    first = await inbox.reply(db, staff, thread_id, R1, "r34-demo-reply-1")
    second = await inbox.reply(db, staff, thread_id, R2, "r34-demo-reply-2-pending") if first["replayed"] else None
    if owner and not first["replayed"]:  # first run: client has read reply 1 only
        await db.ask_seen.update_one({"tenant_id": tid, "user_id": owner["user_id"], "thread_id": thread_id},
                                     {"$set": {"last_seen_at": datetime.now(timezone.utc)}}, upsert=True)
        await asyncio.sleep(0.05)
        second = await inbox.reply(db, staff, thread_id, R2, "r34-demo-reply-2-pending")
    await _event(db, tid, "DemoClaimThreadSeeded", {"claim_id": claim["claim_id"], "thread_id": thread_id},
                 f"r34:claim_thread:{claim['claim_id']}", "r34", "operator")
    return {"claim_id": claim["claim_id"], "claim_reference": claim.get("claim_reference"), "thread_id": thread_id,
            "replayed": first["replayed"] and bool(second and second["replayed"])}


async def main() -> None:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    print(await seed(db))


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
