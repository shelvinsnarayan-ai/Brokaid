"""R3.4 — a short claim thread for the Harbourline demo client's open claim (demo tenant only; idempotent; ledgered).

Client message → broker reply (keeps the reply-by promise) → second broker reply left unread for the demo client.
The thread goes on an open motor claim whose rego is a vehicle on its motor policy (lodges one if none exists).
Also makes CLM-2026-000002 coherent: it sits on the Marine Liability policy, so it becomes a liability claim, and the
earlier R3.4 seed thread on it (motor wording) is archived with a test flag. Every change writes a ledger row.
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
MSG = "Hi — I saw the insurer has made a decision on the Ranger. When will the repairer be in touch?"
R1 = "Thanks for the message. The insurer has approved the repair, and the repairer will call this week to book the Ranger in."
R2 = "Update: the repairer has a slot on Monday morning. The excess for this policy is shown on the claim page."
LIABILITY_REF = "CLM-2026-000002"
LIABILITY_WHAT = "A visitor slipped on a wet pontoon at the marina berth and has made a claim against the business."


async def _open_claim(db, tid):
    from app.kernel import claim_threads as ct, claims as ck
    names = await ct.vehicle_names(db, tid, CID)
    async for c in db.claims.find({"tenant_id": tid, "client_id": CID, "test": {"$ne": True}, "claim_class": "motor",
                                   "stage": {"$nin": ["closed", "withdrawn"]}}).sort("lodged_at", 1):
        rego = str((c.get("incident") or {}).get("rego") or "").upper()
        pol = await db.policies.find_one({"tenant_id": tid, "policy_id": c.get("policy_id")})
        if rego in names and ct.policy_class((pol or {}).get("product")) == "Motor":
            return c
    pol = await db.policies.find_one({"tenant_id": tid, "client_id": CID, "status": "managed", "schedule.vehicles.0": {"$exists": True}})
    v = pol["schedule"]["vehicles"][0]
    body = {"claim_class": "motor", "subtype": "accident", "incident_date": (datetime.now(timezone.utc) - timedelta(days=3)).date().isoformat(),
            "where": "Corner of Bay St and Marine Pde", "what": "Rear-ended at low speed while stopped at lights.", "rego": v["rego"],
            "parties": [], "injuries": False, "safe": True}
    return await ck.lodge(db, tid, client_id=CID, policy=pol, body=body, by="r34-seed", role="client", channel="in_app")


async def _fix_liability_claim(db, tid, _event, ct):
    c = await db.claims.find_one({"tenant_id": tid, "client_id": CID, "claim_reference": LIABILITY_REF})
    if not c:
        return
    if c.get("claim_class") != "liability":
        await db.claims.update_one({"tenant_id": tid, "claim_id": c["claim_id"]}, {"$set": {
            "claim_class": "liability", "subtype": "third_party", "claim_type": "liability_third_party", "description": LIABILITY_WHAT,
            "incident.what": LIABILITY_WHAT, "incident.rego": None}})
        await _event(db, tid, "DemoClaimCorrected", {"claim_id": c["claim_id"], "claim_reference": LIABILITY_REF,
                                                     "from_class": c.get("claim_class"), "to_class": "liability"},
                     f"r34:fix_class:{c['claim_id']}", "r34", "operator")
    from app.scripts.r36_claim_thread_flags import flag_claim_thread_messages
    fixed = await db.events.find_one({"tenant_id": tid, "type": "DemoClaimCorrected", "payload.claim_id": c["claim_id"]})
    # only messages that existed when the claim was corrected; later real messages on this claim stay visible
    await flag_claim_thread_messages(db, tid, _event, ct.thread_id_for(c["claim_id"]),
                                     "r34 demo: motor wording on a liability claim; thread moved", fixed["ts"] if fixed else None)


QA_RX = __import__("re").compile(r"^T\d+ |\bQA\b|BROWSERTEST")


async def _archive_qa_threads(db, tid, _event):
    """Testing-agent chatter on demo claim threads (e.g. 'T34 opening thread lazily') is flagged as test, never deleted."""
    async for e in db.events.find({"tenant_id": tid, "type": "ClientQuestionAsked", "payload.client_id": CID,
                                   "payload.claim_id": {"$exists": True}}).sort("seq", 1):
        thread_id = e["payload"]["envelope_id"]
        if not QA_RX.search(e["payload"].get("message") or ""):
            continue
        kind, ref = ("ask", e["payload"].get("msg_id")) if thread_id.startswith("claim-") else ("thread", thread_id)
        r = await db.test_flags.update_one({"tenant_id": tid, "kind": kind, "ref": ref},
                                           {"$setOnInsert": {"reason": "r34 demo: QA message", "flagged_at": datetime.now(timezone.utc)}}, upsert=True)
        if r.upserted_id:
            await _event(db, tid, "DemoClaimThreadArchived", {"thread_id": thread_id, "kind": kind, "ref": ref},
                         f"r34:archive_qa:{kind}:{ref}", "r34", "operator")


async def seed(db) -> dict:
    from app.kernel import claim_threads as ct, devmode, inbox
    from app.kernel.renewals import _event
    t = await db.tenants.find_one({"slug": "harbourline"})
    if not t or not devmode.is_demo_row(t):
        raise SystemExit("Harbourline demo tenant not found")
    tid = t["tenant_id"]
    await _fix_liability_claim(db, tid, _event, ct)
    await _archive_qa_threads(db, tid, _event)
    claim = await _open_claim(db, tid)
    k = claim["claim_id"][:8]
    owner = await db.users.find_one({"email": OWNER})
    ae = await db.users.find_one({"user_id": claim.get("assigned_ae")}) if claim.get("assigned_ae") else None
    if not ae:
        m = await db.memberships.find_one({"tenant_id": tid, "role": {"$in": ["ae", "principal"]}, "revoked_at": None})
        ae = await db.users.find_one({"user_id": m["user_id"]})
    staff = SimpleNamespace(tenant_id=tid, user_id=ae["user_id"], active_role="ae", client_id=None)
    await ct.post_client(db, tid, user_id=owner["user_id"] if owner else "r34-seed", role="client", claim=claim, message=MSG,
                         msg_id=f"r34-demo-msg-1-{k}", received_at=datetime.now(timezone.utc) - timedelta(hours=20))
    thread_id = ct.thread_id_for(claim["claim_id"])
    first = await inbox.reply(db, staff, thread_id, R1, f"r34-demo-reply-1-{k}")
    second = await inbox.reply(db, staff, thread_id, R2, f"r34-demo-reply-2-{k}") if first["replayed"] else None
    if owner and not first["replayed"]:  # first run: client has read reply 1 only
        await db.ask_seen.update_one({"tenant_id": tid, "user_id": owner["user_id"], "thread_id": thread_id},
                                     {"$set": {"last_seen_at": datetime.now(timezone.utc)}}, upsert=True)
        await asyncio.sleep(0.05)
        second = await inbox.reply(db, staff, thread_id, R2, f"r34-demo-reply-2-{k}")
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
