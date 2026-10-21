"""R3.4d — claim-thread flags are per message; demo state for testing (Harbourline demo only; idempotent; ledgered).

1. Converts any thread-level test flag on a claim-* thread into per-message flags covering only the messages that existed
   when it was set, then removes the thread flag (a fixed per-claim thread id must never hide later real messages).
2. Flags the T38 tester artifacts on CLM-2026-000002's thread.
3. Resets the demo owner's seen marker on the CLM-2026-000013 thread so its latest broker reply is unread ("New") again.
4. Adds a second demo client (owner2@fictionalclient.example) with a motor policy and an open claim, for same-tenant
   cross-client isolation checks.
5. Adds one withdrawn (ended) claim for the main demo client with a short thread, so "Earlier claims" and 409 claim_closed
   can be checked.
Usage: python -m app.scripts.r36_claim_thread_flags
"""
# tenant-safe: system-only (Harbourline resolved by slug; every read/write is tenant-scoped)
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

CID = "00000000-0000-4000-8000-000000000001"
CID2 = "00000000-0000-4000-8000-0000000000c2"
OWNER, OWNER2 = "owner@fictionalclient.example", "owner2@fictionalclient.example"
POL2 = "00000000-0000-4000-8000-0000000002c2"
T38 = (("ask", "b2be42d4-2748-4b4e-be1d-130f8780ba96"), ("reply", "ec9f60ac-0e06-4ff3-963f-c47049b4de22"))
OWNER_AUTH = {"can_approve": True, "can_pay": True, "can_sign": True, "can_change_bank": True, "can_lodge_claim": True}


async def _flag(db, tid, _event, kind, ref, why) -> bool:
    r = await db.test_flags.update_one({"tenant_id": tid, "kind": kind, "ref": ref},
                                       {"$setOnInsert": {"reason": why, "flagged_at": datetime.now(timezone.utc)}}, upsert=True)
    if r.upserted_id:
        await _event(db, tid, "DemoTestDataFlagged", {"kind": kind, "ref": ref, "reason": why}, f"r36:flag:{kind}:{ref}", "r36", "operator")
    return bool(r.upserted_id)


async def flag_claim_thread_messages(db, tid, _event, thread_id: str, why: str, cutoff: datetime | str | None = None) -> int:
    """Per-message test flags for a claim thread, limited to messages at or before `cutoff` (default: now)."""
    cutoff, n = cutoff or datetime.now(timezone.utc), 0
    if isinstance(cutoff, str):
        cutoff = datetime.fromisoformat(cutoff)
    upto = (cutoff if cutoff.tzinfo else cutoff.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()  # events.ts is ISO text
    async for e in db.events.find({"tenant_id": tid, "ts": {"$lte": upto}, "$or": [
            {"type": "ClientQuestionAsked", "payload.envelope_id": thread_id}, {"type": "BrokerReplied", "payload.thread_id": thread_id}]}):
        p = e["payload"]
        kind, ref = ("ask", p.get("msg_id")) if e["type"] == "ClientQuestionAsked" else ("reply", p.get("reply_id"))
        if ref:
            n += await _flag(db, tid, _event, kind, ref, why)
    return n


async def convert_thread_flags(db, tid, _event) -> int:
    n = 0
    async for f in db.test_flags.find({"tenant_id": tid, "kind": "thread", "ref": {"$regex": "^claim-"}}):
        cutoff = f.get("flagged_at") or datetime.now(timezone.utc)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        msgs = await flag_claim_thread_messages(db, tid, _event, f["ref"], f"{f.get('reason') or 'test'} (per message)", cutoff)
        await db.test_flags.delete_one({"_id": f["_id"]})
        await _event(db, tid, "DemoClaimThreadFlagConverted", {"thread_id": f["ref"], "cutoff": cutoff.isoformat(), "messages_flagged": msgs,
                                                               "reason": f.get("reason")}, f"r36:convert:{f['ref']}:{cutoff.isoformat()}",
                     "r36", "operator")
        n += 1
    return n


async def _reset_seen(db, tid, _event, ct) -> str | None:
    c = await db.claims.find_one({"tenant_id": tid, "client_id": CID, "claim_reference": "CLM-2026-000013"})
    owner = await db.users.find_one({"email": OWNER})
    if not c or not owner:
        return None
    thread_id = ct.thread_id_for(c["claim_id"])
    last = await db.events.find_one({"tenant_id": tid, "type": "BrokerReplied", "payload.thread_id": thread_id}, sort=[("seq", -1)])
    if not last:
        return None
    prev = await db.events.find_one({"tenant_id": tid, "type": "BrokerReplied", "payload.thread_id": thread_id,
                                     "seq": {"$lt": last["seq"]}}, sort=[("seq", -1)])
    at = datetime.fromisoformat(last["payload"]["replied_at"])
    # between the previous reply and the latest, so exactly the latest one is unread
    seen_at = datetime.fromisoformat(prev["payload"]["replied_at"]) + (at - datetime.fromisoformat(prev["payload"]["replied_at"])) / 2 \
        if prev else at - timedelta(seconds=1)
    await db.ask_seen.update_one({"tenant_id": tid, "user_id": owner["user_id"], "thread_id": thread_id},
                                 {"$set": {"last_seen_at": seen_at}}, upsert=True)
    await _event(db, tid, "DemoSeenMarkerReset", {"thread_id": thread_id, "user": OWNER, "last_seen_at": seen_at.isoformat()},
                 f"r36:seen:{thread_id}:{last['payload']['reply_id']}:{seen_at.isoformat()}", "r36", "operator")
    return thread_id


async def _second_client(db, tid, _event, ck) -> dict:
    now = datetime.now(timezone.utc)
    await db.clients.update_one({"tenant_id": tid, "client_id": CID2}, {"$setOnInsert": {
        "display_name": "Southbank Signs & Print Pty Ltd", "industry": "signwriting", "domicile": "Melbourne, AU", "jurisdiction_code": "AU",
        "assigned_ae_user_id": (await db.clients.find_one({"tenant_id": tid, "client_id": CID}) or {}).get("assigned_ae_user_id")}}, upsert=True)
    u = await db.users.find_one({"email": OWNER2})
    if not u:
        u = {"user_id": str(uuid.uuid4()), "email": OWNER2, "display_name": "Client (second demo)", "kind": "client", "created_at": now,
             "tenant_id": tid, "disabled": False}
        await db.users.insert_one(dict(u))
    await db.memberships.update_one({"tenant_id": tid, "user_id": u["user_id"], "client_id": CID2}, {"$setOnInsert": {
        "membership_id": str(uuid.uuid4()), "role": "owner", "authority": OWNER_AUTH, "created_at": now, "created_by": "r36",
        "revoked_at": None, "revoked_by": None}}, upsert=True)
    await db.user_client_links.update_one({"tenant_id": tid, "user_id": u["user_id"], "client_id": CID2},
                                          {"$setOnInsert": {"role": "client_owner", "archived_at": None}}, upsert=True)
    await db.policies.update_one({"tenant_id": tid, "policy_id": POL2}, {"$setOnInsert": {
        "client_id": CID2, "status": "managed", "product": "Motor — Commercial Vehicle", "insurer_display": "Fictional Carrier A",
        "inception": now - timedelta(days=60), "expiry": now + timedelta(days=305), "premium_amount": 2140.0, "premium_currency": "AUD",
        "schedule": {"vehicles": [{"rego": "SIG-202", "make": "Toyota", "model": "HiAce", "year": 2022, "market_value": 41000,
                                   "use": "Business use"}], "excess": {"any_one_occurrence": 1000}}}}, upsert=True)
    await db.policies.update_one({"tenant_id": tid, "policy_id": POL2, "schedule.excess": 1000},
                                 {"$set": {"schedule.excess": {"any_one_occurrence": 1000}}})  # first-run shape fix
    c = await db.claims.find_one({"tenant_id": tid, "client_id": CID2, "test": {"$ne": True}})
    if not c:
        pol = await db.policies.find_one({"tenant_id": tid, "policy_id": POL2})
        c = await ck.lodge(db, tid, client_id=CID2, policy=pol, by=u["user_id"], role="client", channel="in_app", body={
            "claim_class": "motor", "subtype": "glass", "incident_date": (now - timedelta(days=2)).date().isoformat(),
            "where": "Hume Hwy near Craigieburn", "what": "A stone cracked the windscreen on the highway.", "rego": "SIG-202"})
    await _event(db, tid, "DemoSecondClientSeeded", {"client_id": CID2, "user": OWNER2, "claim_id": c["claim_id"]},
                 f"r36:second_client:{CID2}", "r36", "operator")
    return {"client_id": CID2, "claim_reference": c["claim_reference"]}


async def _ended_claim(db, tid, _event, ck, ct, inbox) -> dict:
    c = await db.claims.find_one({"tenant_id": tid, "client_id": CID, "test": {"$ne": True}, "description": {"$regex": "^Nail gun"}})
    if not c:
        pol = await db.policies.find_one({"tenant_id": tid, "policy_id": "00000000-0000-4000-8000-000000000150"})
        owner = await db.users.find_one({"email": OWNER})
        c = await ck.lodge(db, tid, client_id=CID, policy=pol, by=owner["user_id"], role="client", channel="in_app", body={
            "claim_class": "property", "subtype": "damage", "incident_date": (datetime.now(timezone.utc) - timedelta(days=40)).date().isoformat(),
            "where": "Slipway workshop", "what": "Nail gun dropped from the scaffold and the housing cracked."})
        await ct.post_client(db, tid, user_id=owner["user_id"], role="client", claim=c, msg_id=f"r36-ended-{c['claim_id'][:8]}",
                             message="Found a receipt — the nail gun is still under the maker's warranty, so we'll go through them instead.")
        ae = await db.users.find_one({"user_id": c.get("assigned_ae")}) if c.get("assigned_ae") else None
        if not ae:
            m = await db.memberships.find_one({"tenant_id": tid, "role": {"$in": ["ae", "principal"]}, "revoked_at": None})
            ae = await db.users.find_one({"user_id": m["user_id"]})
        staff = SimpleNamespace(tenant_id=tid, user_id=ae["user_id"], active_role="ae", client_id=None)
        await inbox.reply(db, staff, ct.thread_id_for(c["claim_id"]), "Thanks, noted. We'll withdraw the claim so it isn't on the record.",
                          f"r36-ended-reply-{c['claim_id'][:8]}")
        c = await ck.withdraw(db, tid, c, reason="Covered by the manufacturer's warranty", by=ae["user_id"], role="ae")
        await db.ask_seen.update_one({"tenant_id": tid, "user_id": owner["user_id"], "thread_id": ct.thread_id_for(c["claim_id"])},
                                     {"$set": {"last_seen_at": datetime.now(timezone.utc)}}, upsert=True)  # ended thread already read
    await _event(db, tid, "DemoEndedClaimSeeded", {"claim_id": c["claim_id"], "stage": c["stage"]}, f"r36:ended:{c['claim_id']}",
                 "r36", "operator")
    return {"claim_reference": c["claim_reference"], "stage": c["stage"]}


async def run(db) -> dict:
    from app.kernel import claim_threads as ct, claims as ck, devmode, inbox
    from app.kernel.renewals import _event
    t = await db.tenants.find_one({"slug": "harbourline"})
    if not t or not devmode.is_demo_row(t):
        raise SystemExit("Harbourline demo tenant not found")
    tid = t["tenant_id"]
    out = {"converted": await convert_thread_flags(db, tid, _event)}
    out["t38_flagged"] = sum([await _flag(db, tid, _event, k, r, "T38 tester artifact") for k, r in T38])
    out["second_client"] = await _second_client(db, tid, _event, ck)
    out["ended_claim"] = await _ended_claim(db, tid, _event, ck, ct, inbox)
    out["seen_reset"] = await _reset_seen(db, tid, _event, ct)
    return out


async def main() -> None:
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    print(await run(db))


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
