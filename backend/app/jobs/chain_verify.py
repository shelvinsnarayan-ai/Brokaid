"""Bounded chain-verify job (R5c-4a — Mongo-only)."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

from app.jobs._tenants import find_by_slug, list_tenants
from app.kernel.locks import Lock
from app.kernel.trail_dispatch import append_via_dispatch, read_events
from app.kernel.verifier import verify_events

logger = logging.getLogger("brokaid.chain_verify")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
)

MAX_RUNTIME_SECONDS_PER_TENANT = 60
VERIFIER_PY = Path(__file__).resolve()


def _verifier_version() -> str:
    return hashlib.sha256(VERIFIER_PY.read_bytes()).hexdigest()[:16]


def _lock_key_for(tenant_id: str) -> int:
    return int(hashlib.blake2b(
        tenant_id.encode(), digest_size=8,
    ).hexdigest(), 16) & 0x7FFFFFFFFFFFFFFF


async def verify_tenant(store, slug: str) -> bool:
    start = datetime.now(timezone.utc)
    t = await find_by_slug(store, slug)
    if t is None:
        logger.error("unknown tenant: %s", slug)
        return False
    tenant_id = t["tenant_id"]
    lock = Lock(
        store, key=_lock_key_for(tenant_id),
        holder_id=f"chain_verify:{tenant_id}:{start.isoformat()}",
        ttl_sec=MAX_RUNTIME_SECONDS_PER_TENANT + 30,
    )
    if not await lock.try_acquire():
        logger.info("tenant %s: chain_verify lock busy — skipping", slug)
        return True
    try:
        _, events = await read_events(store, slug)
        payload = [
            {"event_id": e.event_id, "tenant_id": e.tenant_id, "seq": e.seq,
             "ts": e.ts, "actor": e.actor, "type": e.type,
             "payload": e.payload, "payload_jcs": e.payload_jcs,
             "prev_hash": e.prev_hash, "event_hash": e.event_hash,
             "idempotency_key": e.idempotency_key, "mocked": e.mocked}
            for e in events
        ]
        result = verify_events(payload)
        if not result.ok:
            logger.error(
                "tenant %s FAILED at seq %s (%s): %s",
                slug, result.failure_seq, result.failure_event_id,
                result.reason,
            )
        await append_via_dispatch(
            store, tenant_id, "ChainVerified",
            actor={"kind": "system", "id": "chain_verify",
                   "name": "trAIl-verifier"},
            payload={
                "from_seq": events[0].seq if events else 0,
                "to_seq": events[-1].seq if events else 0,
                "ok": result.ok, "root_hash": result.root_hash,
                "verifier_version": _verifier_version(),
            },
            idempotency_key=f"chain-verify:{slug}:{start.isoformat()}",
        )
        return result.ok
    finally:
        await lock.release()


async def verify_all(store) -> bool:
    from app.jobs.runner import OPS_TENANT_SLUG
    tenants = await list_tenants(store, exclude_slug=OPS_TENANT_SLUG)
    overall_ok = True
    for t in tenants:
        ok = await verify_tenant(store, t["slug"])
        overall_ok = overall_ok and ok
        logger.info("tenant %s: %s", t["slug"], "OK" if ok else "FAIL")
    return overall_ok


async def _open_store():
    raw = os.environ["MONGO_URL"]
    url = raw.replace("://localhost:", "://127.0.0.1:") if "localhost" in raw else raw
    client = AsyncIOMotorClient(url, uuidRepresentation="standard")
    return client[os.environ["DB_NAME"]]


async def _close_store(store) -> None:
    store.client.close()


async def _main() -> int:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    store = await _open_store()
    try:
        return 0 if await verify_all(store) else 1
    finally:
        await _close_store(store)


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
