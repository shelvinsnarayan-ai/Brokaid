"""R1.9 — encrypted per-tenant backups to object storage, restore into a NEW scratch database only, and restore drills.

Archive = AES-256-GCM(gzip(extended-JSON of every tenant-scoped document in every collection)). Key: BACKUP_ENCRYPTION_KEY
(base64, 32 bytes). Manifest (counts, hashes, key id) is kept in the `backups` system collection and next to the archive.
Retention 30 daily + 12 monthly is logical: Emergent object storage has no delete API, so expired archives are marked expired.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import os
import uuid
from datetime import datetime, timezone

from bson import json_util
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from motor.motor_asyncio import AsyncIOMotorClient

from app.kernel import object_store
from app.kernel.trail_dispatch import append_via_dispatch
from app.kernel.verifier import verify_events

SYSTEM_SKIP = {"ops_requests", "ops_errors", "ops_alerts", "backups", "restore_drills", "jobs", "_locks", "eval_runs"}
SCRATCH_PREFIX = "brokaid_restore_"


def _key() -> bytes:
    raw = os.environ.get("BACKUP_ENCRYPTION_KEY") or ""
    k = base64.b64decode(raw) if raw else b""
    if len(k) != 32:
        raise RuntimeError("BACKUP_ENCRYPTION_KEY must be 32 bytes, base64-encoded.")
    return k


def key_id() -> str:
    return hashlib.sha256(_key()).hexdigest()[:12]


def encrypt(plain: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(_key()).encrypt(nonce, plain, b"brokaid-backup-v1")


def decrypt(blob: bytes) -> bytes:
    return AESGCM(_key()).decrypt(blob[:12], blob[12:], b"brokaid-backup-v1")


def now():
    return datetime.now(timezone.utc)


async def _collections(store) -> list[str]:
    return sorted(c for c in await store.list_collection_names() if c not in SYSTEM_SKIP and not c.startswith("system."))


async def backup_tenant(store, tenant_id: str, *, by: str = "nightly") -> dict:
    t = now()
    data, counts = {}, {}
    for name in await _collections(store):
        q = {"tenant_id": tenant_id} if name != "tenants" else {"tenant_id": tenant_id}
        rows = [d async for d in store[name].find(q)]  # tenant-safe: system-only — backup export of one tenant's rows
        if rows:
            data[name], counts[name] = rows, len(rows)
    plain = gzip.compress(json_util.dumps({"tenant_id": tenant_id, "at": t, "collections": data}).encode(), 6)
    blob = encrypt(plain)
    tenant = await store.tenants.find_one({"tenant_id": tenant_id}) or {}  # tenant-safe: system-only
    bid = str(uuid.uuid4())
    path = f"{object_store.APP_NAME}/backups/{tenant_id}/{t:%Y/%m/%d}/{bid}.bin"
    await object_store.put_object(path, blob, "application/octet-stream")
    month_first = not await store.backups.find_one({"tenant_id": tenant_id, "month": f"{t:%Y-%m}"})  # tenant-safe: system-only
    m = {"backup_id": bid, "tenant_id": tenant_id, "tenant_slug": tenant.get("slug"), "at": t, "month": f"{t:%Y-%m}",
         "tier": "monthly" if month_first else "daily", "path": path, "counts": counts, "events": counts.get("events", 0),
         "plain_sha256": hashlib.sha256(plain).hexdigest(), "blob_sha256": hashlib.sha256(blob).hexdigest(), "size": len(blob),
         "key_id": key_id(), "by": by, "expired": False}
    await object_store.put_object(f"{path}.manifest.json", json_util.dumps(m).encode(), "application/json")
    await store.backups.insert_one(dict(m))  # tenant-safe: system-only — backup manifests
    await apply_retention(store, tenant_id)
    m.pop("_id", None)
    return m


async def apply_retention(store, tenant_id: str) -> int:
    # tenant-safe: system-only — logical retention on manifests
    rows = [r async for r in store.backups.find({"tenant_id": tenant_id, "expired": False}).sort("at", -1)]
    keep_daily = {r["backup_id"] for r in rows[:30]}
    keep_monthly = {r["backup_id"] for r in [r for r in rows if r["tier"] == "monthly"][:12]}
    drop = [r["backup_id"] for r in rows if r["backup_id"] not in keep_daily | keep_monthly]
    if drop:
        await store.backups.update_many({"backup_id": {"$in": drop}}, {"$set": {"expired": True, "expired_at": now()}})
    return len(drop)


async def backup_all(store, by: str = "nightly") -> list[dict]:
    out = []
    async for t in store.tenants.find({}, {"tenant_id": 1}):  # tenant-safe: system-only
        out.append(await backup_tenant(store, t["tenant_id"], by=by))
    return out


async def load(store, backup_id: str) -> tuple[dict, dict]:
    m = await store.backups.find_one({"backup_id": backup_id}, {"_id": 0})  # tenant-safe: system-only
    if not m:
        raise LookupError("backup_not_found")
    blob, _ = await object_store.get_object(m["path"])
    if hashlib.sha256(blob).hexdigest() != m["blob_sha256"]:
        raise ValueError("Archive hash doesn't match the manifest.")
    plain = decrypt(blob)
    if hashlib.sha256(plain).hexdigest() != m["plain_sha256"]:
        raise ValueError("Decrypted archive hash doesn't match the manifest.")
    return m, json_util.loads(gzip.decompress(plain))


async def restore(store, backup_id: str, target_db: str) -> dict:
    """Restore into a NEW, EMPTY scratch database. Never the live database."""
    live = os.environ["DB_NAME"]
    if target_db == live or not target_db.startswith(SCRATCH_PREFIX):
        raise ValueError(f"Restores only go to a new scratch database named {SCRATCH_PREFIX}…, never the live one.")
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    scratch = client[target_db]
    if await scratch.list_collection_names():
        raise ValueError("The scratch database isn't empty.")
    m, doc = await load(store, backup_id)
    counts = {}
    for name, rows in doc["collections"].items():
        if rows:
            await scratch[name].insert_many(rows)  # tenant-safe: system-only — scratch restore target
            counts[name] = len(rows)
    return {"backup_id": backup_id, "target_db": target_db, "counts": counts, "manifest_counts": m["counts"], "scratch": scratch,
            "client": client, "manifest": m}


async def drill(store, tenant_id: str, *, by: str = "drill") -> dict:
    m = await store.backups.find_one({"tenant_id": tenant_id, "expired": False}, {"_id": 0}, sort=[("at", -1)])  # tenant-safe: system-only
    t0, result = now(), {"tenant_id": tenant_id, "at": now(), "by": by, "backup_id": (m or {}).get("backup_id")}
    target = f"{SCRATCH_PREFIX}{uuid.uuid4().hex[:10]}"
    r = None
    try:
        if not m:
            raise ValueError("No backup to restore.")
        r = await restore(store, m["backup_id"], target)
        evs = [e async for e in r["scratch"].events.find({"tenant_id": tenant_id}, {"_id": 0}).sort("seq", 1)]  # tenant-safe: system-only
        v = verify_events(evs)
        counts_ok = r["counts"] == r["manifest_counts"]
        result.update(ok=bool(v.ok and counts_ok), chain_ok=bool(v.ok), counts_ok=counts_ok, events=len(evs),
                      collections=len(r["counts"]), reason=None if v.ok else v.reason)
    except Exception as e:  # noqa: BLE001 — a failed drill is a recorded FAIL
        result.update(ok=False, reason=f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        if r is not None:
            await r["client"].drop_database(target)
            r["client"].close()
    result["result"] = "PASS" if result.get("ok") else "FAIL"
    result["ms"] = int((now() - t0).total_seconds() * 1000)
    await store.restore_drills.insert_one(dict(result))  # tenant-safe: system-only
    await append_via_dispatch(store, tenant_id, "BackupRestoreDrill", actor={"kind": "system", "id": "backup", "name": "backup"},
                              payload={k: result.get(k) for k in ("backup_id", "result", "chain_ok", "counts_ok", "events", "reason")},
                              idempotency_key=f"drill:{tenant_id}:{t0.isoformat()}")
    result.pop("_id", None)
    return result
