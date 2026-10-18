"""R2.0 follow-up: demo seeds refuse non-demo tenants; a company name never matches a person's name on file."""
from __future__ import annotations

import uuid

import pytest

from app.kernel import devmode
from app.kernel.acceptance import name_match
from app.seeds import (
    seed_client_mongo, seed_historical_mongo, seed_inbox_mongo, seed_kai_motor_mongo, seed_memberships_mongo,
    seed_org_mongo, seed_policy_managed_mongo, seed_policy_tracked_mongo, seed_things_mongo, seed_vulnerable_mongo,
)
from app.seeds.seed_burmac_extras_mongo import seed_burmac_extras

CID = "00000000-0000-4000-8000-000000000001"
SEEDS = [
    lambda db, t: seed_org_mongo.seed_mongo(db, t, "b"),
    lambda db, t: seed_client_mongo.seed_mongo(db, t),
    lambda db, t: seed_memberships_mongo.seed_mongo(db, t),
    lambda db, t: seed_policy_managed_mongo.seed_mongo(db, t, CID),
    lambda db, t: seed_policy_tracked_mongo.seed_mongo(db, t, CID),
    lambda db, t: seed_kai_motor_mongo.seed_mongo(db, t, CID),
    lambda db, t: seed_things_mongo.seed_mongo(db, t, CID),
    lambda db, t: seed_inbox_mongo.seed_mongo(db, t, CID),
    lambda db, t: seed_vulnerable_mongo.seed_mongo(db, t),
    lambda db, t: seed_historical_mongo.seed_historical_decisions_mongo(db, t),
    lambda db, t: seed_historical_mongo.seed_queue_cards_mongo(db, t, CID),
    lambda db, t: seed_burmac_extras(db, t),
]


@pytest.mark.parametrize("row", [{"slug": "burmac"}, {"slug": "acme-wizard", "kind": "brokerage"},
                                 {"slug": "platform", "kind": "platform"}, None])
async def test_every_demo_seed_refuses_non_demo_tenant(mongo_db, row):
    tid = str(uuid.uuid4())
    if row:
        slug = row["slug"] if row["slug"] == "burmac" else f"{row['slug']}-{tid[:4]}"
        await mongo_db.tenants.insert_one({"tenant_id": tid, **row, "slug": slug})  # tenant-safe: system-only
    before = {c: await mongo_db[c].count_documents({"tenant_id": tid})  # tenant-safe: system-only
              for c in ("clients", "policies", "memberships", "events")}
    for seed in SEEDS:
        with pytest.raises(RuntimeError, match="non-demo tenant"):
            await seed(mongo_db, tid)
    after = {c: await mongo_db[c].count_documents({"tenant_id": tid}) for c in before}  # tenant-safe: system-only
    assert after == before


async def test_demo_seed_allowed_on_demo_tenant(mongo_db):
    tid = str(uuid.uuid4())
    await mongo_db.tenants.insert_one({"tenant_id": tid, "slug": f"ci-{tid[:6]}"})  # tenant-safe: system-only
    await devmode.require_demo_seed(mongo_db, tid)


@pytest.mark.parametrize("typed,expect", [
    ("Harbour Marine Services Pty Ltd", "mismatch"),
    ("Harbour Marine Services", "mismatch"),
    ("Kai", "mismatch"),
    ("Owner", "mismatch"),
    ("Kai Owner", "exact"),
    ("kai owner", "exact"),
    ("Kai J. Owner", "close"),
])
def test_company_name_never_matches_person(typed, expect):
    assert name_match(typed, "Kai Owner") == expect
