"""R1.4 — tenant clean start: archive legacy synthetic rows. Dry-run first, nothing deleted.

Execution is refused unless the tenant is a test tenant or its slug is listed in
CLEAN_START_APPROVED_SLUGS (set only after the owner approves).
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone

from app.db.tenant_safe import wrap
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch

# collection → (filter for live rows, id field)
TARGETS = {
    "clients": ({"archived_at": None, "external.iadviser_client_code": {"$exists": False}}, "client_id"),
    "policies": ({"archived_at": None, "external.iadviser_policy_number": {"$exists": False}}, "policy_id"),
    "things": ({"archived_at": None, "external.iadviser_vehicle_key": {"$exists": False}}, "thing_id"),
    "claims": ({"archived_at": None}, "claim_id"),
    "memberships": ({"revoked_at": None}, "membership_id"),
    "user_client_links": ({"archived_at": None}, "user_id"),
    "user_brokerage_roles": ({"active": {"$ne": False}, "archived_at": None}, "user_id"),
    "users": ({"email": {"$regex": r"\.example$"}, "archived_at": None}, "user_id"),
}


def allowed(tenant: dict) -> bool:
    approved = {s.strip() for s in (os.environ.get("CLEAN_START_APPROVED_SLUGS") or "").split(",") if s.strip()}
    return bool(tenant.get("test")) or tenant["slug"] in approved


async def _scan(safe) -> tuple[dict, str]:
    counts, ids = {}, []
    for coll, (flt, key) in TARGETS.items():
        rows = [str(d.get(key)) async for d in safe[coll].find(flt, {key: 1})]
        counts[coll] = len(rows)
        ids += [f"{coll}:{i}" for i in sorted(rows)]
    return counts, hashlib.sha256("\n".join(ids).encode()).hexdigest()


async def dry_run(store, tenant_id: str, user_id: str) -> dict:
    safe = wrap(store)
    async with use_tenant(tenant_id):
        tenant = await safe.system("tenants").find_one({"tenant_id": tenant_id})
        counts, h = await _scan(safe)
        doc = {"dry_run_id": str(uuid.uuid4()), "counts": counts, "ids_sha256": h, "by": user_id,
               "at": datetime.now(timezone.utc), "executed": False}
        await safe.clean_start_runs.insert_one(dict(doc))
    doc.pop("_id", None)
    return {**doc, "tenant": tenant["display_name"], "slug": tenant["slug"], "allowed": allowed(tenant)}


class Refused(Exception):
    pass


async def execute(store, tenant_id: str, user_id: str, role: str, dry_run_id: str, confirm: str) -> dict:
    safe = wrap(store)
    async with use_tenant(tenant_id):
        tenant = await safe.system("tenants").find_one({"tenant_id": tenant_id})
        if not allowed(tenant):
            raise Refused("Clean start isn't approved for this brokerage yet.")
        if (confirm or "").strip().lower() != tenant["slug"]:
            raise Refused(f"Type the brokerage code '{tenant['slug']}' to confirm.")
        dr = await safe.clean_start_runs.find_one({"dry_run_id": dry_run_id, "executed": False})
        if not dr:
            raise Refused("Run the dry run first.")
        counts, h = await _scan(safe)
        if h != dr["ids_sha256"]:
            raise Refused("The data changed since the dry run. Run the dry run again.")
        now = datetime.now(timezone.utc)
        tag = {"archived_at": now, "archived_by": user_id, "archived_reason": "legacy_synthetic"}
        for coll, (flt, _) in TARGETS.items():
            if coll == "memberships":
                await safe.memberships.update_many(flt, {"$set": {"revoked_at": now, "revoked_by": "clean_start"}})
            elif coll == "user_brokerage_roles":
                await safe[coll].update_many(flt, {"$set": {**tag, "active": False}})
            elif coll == "users":
                await safe.users.update_many(flt, {"$set": {**tag, "disabled": True}})
            elif coll == "policies":
                async for p in safe.policies.find(flt, {"policy_id": 1, "status": 1}):
                    await safe.policies.update_one({"policy_id": p["policy_id"]},
                                                   {"$set": {**tag, "status": "archived", "archived_prev_status": p.get("status")}})
            else:
                await safe[coll].update_many(flt, {"$set": tag})
        await append_via_dispatch(store, tenant_id, "TenantDataArchived",
                                  actor={"kind": "human", "id": user_id, "name": role},
                                  payload={"counts": counts, "ids_sha256": h, "reason": "legacy_synthetic",
                                           "dry_run_id": dry_run_id, "deleted": 0},
                                  idempotency_key=f"clean_start:{dry_run_id}")
        await safe.clean_start_runs.update_one({"dry_run_id": dry_run_id}, {"$set": {"executed": True, "executed_at": now}})
    return {"archived": counts, "ids_sha256": h}
