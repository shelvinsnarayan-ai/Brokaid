"""R0.6 — G8 Things seed for the Harbourline demo tenant. Idempotent.

Kai (Harbour Marine): Hilux SR5 + D-Max (motor 104), Workshop + Marine
liability (101), Cyber held elsewhere (102), drone not covered.
Ravi (Narayan household): home (105) and car (106).
Vehicle schedule_items[].fields are read from policy 104's schedule, so the
policy schedule stays the single source for vehicle values.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.kernel.trail_mongo import DuplicateIdempotencyKey
from app.seeds import seed_kai_motor_mongo as motor

MARINE = "00000000-0000-4000-8000-000000000101"
CYBER = "00000000-0000-4000-8000-000000000102"
MOTOR = motor.POLICY_ID
NARAYAN = "00000000-0000-4000-8000-000000000201"
HOME = "00000000-0000-4000-8000-000000000105"
CAR = "00000000-0000-4000-8000-000000000106"
ACTOR = {"kind": "system", "id": "seed", "name": "r06-things-seed"}


def _vehicle_thing(client_id: str, v: dict, sched: dict) -> dict:
    return {
        "thing_id": f"thing-veh-{v['rego'].lower()}", "client_id": client_id, "group": "vehicle",
        "name": v["model"] if v["make"] == "Toyota" else f"{v['make']} {v['model']}",
        "identifiers": {"rego": v["rego"], "vin": v["vin"]}, "status": "covered",
        "template": "motor",
        "template_fields": {"cover_level": "Comprehensive", "who_can_drive": sched["who_can_drive"],
                            "restriction": sched["restriction"]},
        "schedule_items": [{"policy_id": MOTOR, "section": "vehicles", "ref": v["rego"],
                            "fields": {"make": v["make"], "model": v["model"], "year": v["year"],
                                       "agreed_value": v["agreed_value"], "excess": v["excess"],
                                       "use": v["use"]}}],
    }


def kai_things(client_id: str, sched: dict) -> list[dict]:
    return [_vehicle_thing(client_id, v, sched) for v in sched["vehicles"]] + [
        {"thing_id": "thing-place-workshop", "client_id": client_id, "group": "place",
         "name": "Workshop, 12 Harris St", "identifiers": {"address": "12 Harris St, Pyrmont NSW 2009"},
         "status": "covered", "template": "liability",
         "template_fields": {"where_what": "Repair and servicing at 12 Harris St and at clients' berths in NSW",
                             "exclusion": "Racing excluded"},
         "schedule_items": [{"policy_id": MARINE, "section": "premises",
                             "fields": {"limit": 20_000_000, "excess": 2_500}}]},
        {"thing_id": "thing-biz-marine", "client_id": client_id, "group": "business",
         "name": "Marine liability", "identifiers": {"abn": "00 000 000 000"},
         "status": "covered", "template": "liability",
         "template_fields": {"where_what": "Repair and servicing at 12 Harris St and at clients' berths in NSW",
                             "exclusion": "Racing excluded"},
         "schedule_items": [{"policy_id": MARINE, "section": "liability",
                             "fields": {"limit": 20_000_000, "excess": 2_500}}]},
        {"thing_id": "thing-biz-cyber", "client_id": client_id, "group": "business",
         "name": "Cyber", "identifiers": {}, "status": "held_elsewhere", "template": "held_elsewhere",
         "schedule_items": [{"policy_id": CYBER, "section": "cyber", "fields": {}}]},
        {"thing_id": "thing-nc-drone", "client_id": client_id, "group": "not_covered",
         "name": "Survey drone", "identifiers": {"serial": "DRN-0000-FICT"}, "status": "not_covered",
         "template": "not_covered", "told_us_on": "2026-09-02", "schedule_items": []},
    ]


def narayan_things() -> list[dict]:
    return [
        {"thing_id": "thing-place-narayan-home", "client_id": NARAYAN, "group": "place",
         "name": "Home, 14 Wattle St", "identifiers": {"address": "14 Wattle St, Epping NSW 2121"},
         "status": "covered", "template": "home",
         "template_fields": {"cover_level": "Building and contents", "flood": "Flood included",
                             "occupancy": "owner-occupied"},
         "schedule_items": [{"policy_id": HOME, "section": "building",
                             "fields": {"building_sum": 900_000, "contents_sum": 120_000}}]},
        {"thing_id": "thing-veh-narayan-cx5", "client_id": NARAYAN, "group": "vehicle",
         "name": "Mazda CX-5", "identifiers": {"rego": "NAR-001"}, "status": "covered",
         "template": "motor",
         "template_fields": {"cover_level": "Comprehensive", "who_can_drive": "Ravi and Meena Narayan",
                             "restriction": "drivers under 25 pay an extra excess"},
         "schedule_items": [{"policy_id": CAR, "section": "vehicles", "ref": "NAR-001",
                             "fields": {"agreed_value": 38_000, "excess": 750}}]},
    ]


def _policy(pid: str, product: str, premium: float, now) -> dict:
    return {"policy_id": pid, "client_id": NARAYAN, "status": "managed", "product": product,
            "insurer_display": "Fictional Carrier B", "inception": now - timedelta(days=60),
            "expiry": now + timedelta(days=305), "premium_amount": premium, "premium_currency": "AUD",
            "premium_status": "current", "schedule": {}, "imported_from": None, "fictional": True}


async def seed_mongo(db, tenant_id: str, client_id: str) -> int:
    from app.kernel import devmode
    await devmode.require_demo_seed(db, tenant_id)
    safe = wrap(db)
    now = datetime.now(timezone.utc)
    async with use_tenant(tenant_id):
        if not await safe.clients.find_one({"client_id": NARAYAN}):
            await safe.clients.insert_one({"client_id": NARAYAN, "display_name": "Narayan household",
                                           "industry": "Household", "domicile": "AU",
                                           "jurisdiction_code": "AU", "classification": "retail"})
        for pid, prod, prem in ((HOME, "Home — Building and contents", 2_140.0),
                                (CAR, "Motor — Private comprehensive", 1_260.0)):
            if not await safe.policies.find_one({"policy_id": pid}):
                await safe.policies.insert_one(_policy(pid, prod, prem, now))
        sched = (await safe.policies.find_one({"policy_id": MOTOR}) or {}).get("schedule") or motor.SCHEDULE
        made = 0
        for t in kai_things(client_id, sched) + narayan_things():
            if await safe.things.find_one({"thing_id": t["thing_id"]}):
                continue
            await safe.things.insert_one({**t, "worth_knowing": [], "documents": [],
                                          "interested_parties": [], "told_us_on": t.get("told_us_on"),
                                          "provenance": {"source": "seed", "seed": "r06-things", "at": now.isoformat()}})
            made += 1
            try:
                await append_via_dispatch(db, tenant_id, "ThingRecorded", actor=ACTOR,
                                          payload={"thing_id": t["thing_id"], "client_id": t["client_id"],
                                                   "group": t["group"], "name": t["name"],
                                                   "policy_ids": [i["policy_id"] for i in t["schedule_items"]],
                                                   "fictional": True},
                                          idempotency_key=f"seed:r06:thing:{t['thing_id']}")
            except DuplicateIdempotencyKey:
                pass
    return made
