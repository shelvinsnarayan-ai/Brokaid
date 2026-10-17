"""R1.4 — iAdviser import + clean start on a TEST tenant (synthetic export with deliberate errors)."""
from __future__ import annotations

import hashlib
import uuid

import httpx
import os
import pytest

from app.db.tenant_safe import wrap
from app.kernel import iadviser as ia
from app.kernel import tenant_archive, trail_mongo
from app.kernel.tenant_ctx import use_tenant
from app.scripts import iadviser_synth as synth


async def _tenant(db, *, test=True) -> tuple[str, str]:
    tid, slug = str(uuid.uuid4()), f"test-imp-{uuid.uuid4().hex[:6]}"
    async with use_tenant(tid):
        safe = wrap(db)
        await trail_mongo.create_tenant(safe, slug=slug, display_name="Import Test Brokers", tenant_id_override=tid)
        await safe.system("tenants").update_one({"tenant_id": tid}, {"$set": {"test": test}})
        await safe.users.insert_one({"user_id": "u-tess", "kind": "operator", "name": "Tess Account",
                                     "email": "tess@importtest.example"})
    return tid, slug


def _plans(data: bytes, name="x.xlsx") -> list[dict]:
    out = []
    for sh in ia.parse(data, name):
        ent = ia.detect_entity(sh["name"], sh["headers"])
        out.append({**sh, "entity": ent, "mapping": ia.suggest(sh["headers"], ent)})
    return out


async def _validate(db, tid, data):
    run_id, sha = str(uuid.uuid4()), hashlib.sha256(data).hexdigest()
    counts = await ia.validate(db, tid, run_id=run_id, file_sha=sha, sheets=_plans(data))
    async with use_tenant(tid):
        await wrap(db).import_runs.insert_one({"run_id": run_id, "file_sha": sha, "status": "validated", "counts": counts,
                                               "created_by": "u-op"})
    return run_id, counts


async def _verify_chain(db, slug) -> bool:
    from app.kernel.trail_dispatch import read_events
    from app.kernel.verifier import verify_events
    _, evs = await read_events(db, slug)
    return verify_events([{"event_id": e.event_id, "tenant_id": e.tenant_id, "seq": e.seq, "ts": e.ts, "actor": e.actor,
                           "type": e.type, "payload": e.payload, "payload_jcs": e.payload_jcs, "prev_hash": e.prev_hash,
                           "event_hash": e.event_hash} for e in evs]).ok


def test_validators():
    assert ia.abn("51 824 753 556") == "51824753556"
    for bad in ("51 824 753 557", "1234"):
        with pytest.raises(ia.Bad):
            ia.abn(bad)
    assert ia.a_date("03/04/2026").isoformat() == "2026-04-03" and ia.a_date("2026-04-03").month == 4
    assert ia.money("$1,234.50") == 1234.5
    with pytest.raises(ia.Bad):
        ia.money("-$5")
    assert ia.rego("abc-123") == "ABC123"
    with pytest.raises(ia.Bad):
        ia.rego("TOO-LONG-PLATE")
    m = ia.suggest(["Policy No", "Insurer/Underwriter", "Class", "Inception", "Expiry", "Base Premium", "Client Code"], "policies")
    assert m["policy_number"] == "Policy No" and m["insurer"] == "Insurer/Underwriter" and m["base_premium"] == "Base Premium"


@pytest.mark.asyncio
async def test_synthetic_export_imports_reimports_and_updates(mongo_db):
    db = mongo_db
    tid, slug = await _tenant(db)
    data = synth.xlsx_bytes(synth.build())
    run1, c = await _validate(db, tid, data)
    assert c["clients"]["rows"] == 220 and c["clients"]["error"] == 3 and c["clients"]["warn"] >= 1
    assert c["policies"]["rows"] == 431 and c["policies"]["error"] >= 5
    assert c["vehicles"]["rows"] == 170 and c["vehicles"]["error"] >= 1
    async with use_tenant(tid):
        reasons = [i["text"] async for r in wrap(db).import_rows.find({"run_id": run1, "status": {"$ne": "ok"}}) for i in r["issues"]]
    for want in ("checksum", "11 digits", "Missing name", "isn't dd/mm/yyyy", "before inception", "negative",
                 "No client with code C99999", "Duplicate", "valid Australian plate", "17 characters", "Unknown Person"):
        assert any(want in t for t in reasons), want
    await ia.commit_run(db, tid, run1)
    async with use_tenant(tid):
        safe = wrap(db)
        n_cl, n_pol = await safe.clients.count_documents({}), await safe.policies.count_documents({})
        n_th = await safe.things.count_documents({"group": "vehicle"})
        n_ev = await safe.events.count_documents({})
        assert n_cl == 217 and n_pol >= 400 and n_th >= 150
        cl = await safe.clients.find_one({"external.iadviser_client_code": "C00001"})
        assert cl["assigned_ae_user_id"] == "u-tess" and cl["contacts"][0]["login"] is None
        ev = await safe.events.find_one({"type": "PolicyImported"})
        assert ev["payload"]["data_basis"].startswith("iAdviser import ")
    assert await _verify_chain(db, slug)

    # re-import of the same file: nothing changes
    run2, c2 = await _validate(db, tid, data)
    assert all(v["create"] == 0 and v["update"] == 0 for v in c2.values())
    assert (await ia.commit_run(db, tid, run2))["written"] == 0
    async with use_tenant(tid):
        assert await wrap(db).events.count_documents({}) == n_ev

    # a changed row shows a diff, then updates
    d = synth.build()
    d["Policies"][1][6] = "$1,111.00"
    run3, c3 = await _validate(db, tid, synth.xlsx_bytes(d))
    assert c3["policies"]["update"] == 1 and sum(v["update"] for v in c3.values()) == 1
    async with use_tenant(tid):
        row = await wrap(db).import_rows.find_one({"run_id": run3, "action": "update"})
    assert row["key"] == "P000002" and row["diff"]["base_premium"][1] == 1111.0
    await ia.commit_run(db, tid, run3)
    async with use_tenant(tid):
        pol = await wrap(db).policies.find_one({"external.iadviser_policy_number": "P000002"})
        assert pol["base_premium"] == 1111.0
        assert await wrap(db).events.count_documents({"type": "PolicyUpdated", "payload.policy_id": pol["policy_id"],
                                                      "payload.changed": "base_premium"}) == 1
    assert await _verify_chain(db, slug)


@pytest.mark.asyncio
async def test_imported_motor_policy_vehicle_swap_quotes_and_renders(mongo_db):
    from app.kernel import things
    from app.kernel.mocks.carrier_channel import submit_endorsement
    from app.kernel.policy_field_guard import validate_requested_changes
    db = mongo_db
    tid, _ = await _tenant(db)
    run, _ = await _validate(db, tid, synth.xlsx_bytes(synth.build()))
    await ia.commit_run(db, tid, run)
    async with use_tenant(tid):
        safe = wrap(db)
        pol = await safe.policies.find_one({"schedule.vehicles.0": {"$exists": True}})
        thing = await safe.things.find_one({"client_id": pol["client_id"], "group": "vehicle"})
    held = pol["schedule"]["vehicles"][0]
    changes = [{"field_path": "schedule.vehicles", "operation": "replace", "from_value": {"rego": held["rego"]},
                "to_value": {"rego": "NEW22X", "make": "Toyota", "model": "Hilux", "year": 2024}}]
    validate_requested_changes(pol, changes)
    quote = await submit_endorsement(envelope_id=str(uuid.uuid4()), submission={"requested_changes": changes}, policy_current=pol)
    assert quote["quote_id"] and quote.get("carrier") is not None or quote["quote_id"]
    view = things.render(thing, {pol["policy_id"]: pol})
    assert view and pol["insurer_display"].startswith("Fictional Carrier")


@pytest.mark.asyncio
async def test_clean_start_dry_run_then_archive_on_test_tenant_only(mongo_db):
    db = mongo_db
    tid, slug = await _tenant(db)
    async with use_tenant(tid):
        safe = wrap(db)
        for i in range(5):
            await safe.clients.insert_one({"client_id": f"legacy-c{i}", "display_name": f"Legacy {i}"})
        for i in range(6):
            await safe.policies.insert_one({"policy_id": f"legacy-p{i}", "client_id": "legacy-c0", "status": "managed"})
        await safe.memberships.insert_one({"membership_id": "m1", "client_id": "legacy-c0", "user_id": "u1", "role": "owner",
                                           "revoked_at": None})
    dr = await tenant_archive.dry_run(db, tid, "u-op")
    assert dr["counts"]["clients"] == 5 and dr["counts"]["policies"] == 6 and dr["counts"]["memberships"] == 1 and dr["allowed"]
    with pytest.raises(tenant_archive.Refused):
        await tenant_archive.execute(db, tid, "u-op", "operator", dr["dry_run_id"], "wrong")
    out = await tenant_archive.execute(db, tid, "u-op", "operator", dr["dry_run_id"], slug)
    assert out["archived"]["clients"] == 5
    async with use_tenant(tid):
        safe = wrap(db)
        assert await safe.clients.count_documents({}) == 5 and await safe.clients.count_documents({"archived_at": None}) == 0
        assert await safe.policies.count_documents({"status": "archived"}) == 6
        assert (await safe.memberships.find_one({"membership_id": "m1"}))["revoked_at"] is not None
        ev = await safe.events.find_one({"type": "TenantDataArchived"})
        assert ev["payload"]["counts"]["policies"] == 6 and ev["payload"]["deleted"] == 0 and ev["payload"]["ids_sha256"]
    with pytest.raises(tenant_archive.Refused):  # a dry run is single-use
        await tenant_archive.execute(db, tid, "u-op", "operator", dr["dry_run_id"], slug)
    real, real_slug = await _tenant(db, test=False)
    dr2 = await tenant_archive.dry_run(db, real, "u-op")
    assert dr2["allowed"] is False
    with pytest.raises(tenant_archive.Refused):
        await tenant_archive.execute(db, real, "u-op", "operator", dr2["dry_run_id"], real_slug)


BASE = os.environ.get("REACT_APP_BACKEND_URL", "")


def _as(email):
    c = httpx.Client(base_url=BASE, timeout=60)
    assert c.post("/api/auth/dev/impersonate", json={"email": email}).status_code == 200
    return c


def test_import_api_roundtrip_csv():
    op = _as("operator@harbourline.example")
    d = synth.build(n_clients=30, n_policies=0)
    up = op.post("/api/o/import/files", files={"file": ("clients.csv", synth.csv_bytes("Clients", d["Clients"]), "text/csv")})
    assert up.status_code == 200, up.text
    f = up.json()
    sh = f["sheets"][0]
    assert sh["entity"] == "clients" and sh["mapping"]["abn"] == "ABN" and sh["row_count"] == 30
    body = {"file_id": f["file_id"], "sheets": [{"name": sh["name"], "entity": "clients", "mapping": sh["mapping"]}],
            "save_profile": "iAdviser default"}
    run = op.post("/api/o/import/runs", json=body).json()
    assert run["counts"]["clients"]["error"] == 3
    csv_ = op.get(f"/api/o/import/runs/{run['run_id']}/errors.csv")
    assert csv_.status_code == 200 and "checksum" in csv_.text and csv_.text.count("\n") >= 4
    assert op.post(f"/api/o/import/runs/{run['run_id']}/commit").json()["status"] == "committing"
    import time
    for _ in range(60):
        if op.get(f"/api/o/import/runs/{run['run_id']}").json()["status"] == "committed":
            break
        time.sleep(0.5)
    assert op.get(f"/api/o/import/runs/{run['run_id']}").json()["status"] == "committed"
    again = op.post("/api/o/import/runs", json=body).json()
    assert again["counts"]["clients"]["create"] == 0 and again["counts"]["clients"]["unchanged"] == 27
    assert any(p["name"] == "iAdviser default" for p in op.get("/api/o/import/profiles").json()["profiles"])
    rec = op.get(f"/api/o/import/runs/{again['run_id']}/reconciliation").json()
    assert rec["not_in_file"]["clients"] == []
    # AE can't import; client can't either
    assert _as("ae@harbourline.example").get("/api/o/import/runs").status_code == 403
    assert _as("owner@fictionalclient.example").get("/api/o/import/runs").status_code in (401, 403)
    # clean start: Principal can't, Operator dry-run is fine; harbourline isn't a test tenant → execute refused
    assert _as("principal@harbourline.example").get("/api/o/clean-start/dry-run").status_code == 403
    dr = op.get("/api/o/clean-start/dry-run").json()
    assert dr["allowed"] is False
    r = op.post("/api/o/clean-start", json={"dry_run_id": dr["dry_run_id"], "confirm": dr["slug"]})
    assert r.status_code == 409


DEMO = ("owner@fictionalclient.example", "ae@harbourline.example", "principal@harbourline.example", "operator@harbourline.example")


def _snapshot() -> dict:
    kai = _as(DEMO[0])
    out = {"kai_me": len(kai.get("/api/c/me").json()["memberships"]), "kai_things": kai.get("/api/c/things").status_code}
    for e in DEMO[1:]:
        r = _as(e).get("/api/o/clients")
        out[e] = (r.status_code, len(r.json().get("clients", [])))
    return out


def test_clean_start_spares_demo_users_shared_with_harbourline():
    """Clean start on a tenant whose demo roles point at Harbourline's demo people: only that tenant's
    memberships/roles/user rows change; identities and Harbourline access are untouched."""
    import asyncio

    from motor.motor_asyncio import AsyncIOMotorClient

    from app.kernel import auth_flows as af
    before = _snapshot()

    async def go():
        db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
        hl = await db.tenants.find_one({"slug": "harbourline"})
        idents = {e: await db.auth_identities.find_one({"email": e}, {"_id": 0}) for e in DEMO}
        hl_users = [u async for u in db.users.find({"tenant_id": hl["tenant_id"], "email": {"$in": list(DEMO)}}, {"_id": 0})]
        tid, slug = await _tenant(db)
        async with use_tenant(tid):
            safe = wrap(db)
            await safe.clients.insert_one({"client_id": "shared-c", "display_name": "Legacy shared"})
            for e in DEMO:
                uid = str(uuid.uuid4())
                staff = not e.startswith("owner")
                await safe.users.insert_one({"user_id": uid, "email": e, "kind": "operator" if staff else "client", "display_name": e})
                if staff:
                    await safe.user_brokerage_roles.insert_one({"user_id": uid, "brokerage_id": "b", "role": e.split("@")[0], "active": True})
                else:
                    await safe.user_client_links.insert_one({"user_id": uid, "client_id": "shared-c"})
                    await safe.memberships.insert_one({"membership_id": str(uuid.uuid4()), "user_id": uid, "client_id": "shared-c",
                                                       "role": "owner", "revoked_at": None})
        dr = await tenant_archive.dry_run(db, tid, "u-op")
        assert dr["counts"]["users"] >= 4 and dr["counts"]["user_brokerage_roles"] == 3 and dr["counts"]["memberships"] == 1
        await tenant_archive.execute(db, tid, "u-op", "operator", dr["dry_run_id"], slug)
        assert {e: await db.auth_identities.find_one({"email": e}, {"_id": 0}) for e in DEMO} == idents
        assert [u async for u in db.users.find({"tenant_id": hl["tenant_id"], "email": {"$in": list(DEMO)}}, {"_id": 0})] == hl_users
        for e in DEMO:
            slugs = {t["slug"] for t in await af.tenant_options(db, e)}
            assert "harbourline" in slugs and slug not in slugs, (e, slugs)
        async with use_tenant(tid):
            assert await wrap(db).users.count_documents({"email": {"$in": list(DEMO)}, "disabled": True}) == 4
    asyncio.run(go())
    assert _snapshot() == before
