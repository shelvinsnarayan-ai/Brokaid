"""Test-flagged data (synthetic release-check client, its policies and envelopes) stays out of normal views and counts."""
from __future__ import annotations


async def test_client_ids(safe) -> list[str]:
    """Caller must be inside use_tenant."""
    return [c["client_id"] async for c in safe.clients.find({"test": True}, {"client_id": 1})]


async def test_event_filter(safe) -> dict:
    """Mongo filter that drops ledger events belonging to test clients, policies or envelopes. Inside use_tenant."""
    tids = await test_client_ids(safe)
    pids = [p["policy_id"] async for p in safe.policies.find({"test": True}, {"policy_id": 1})]
    envs = [f["ref"] async for f in safe.test_flags.find({"kind": "envelope"}, {"ref": 1})]
    return {"payload.client_id": {"$nin": tids}, "payload.policy_id": {"$nin": pids}, "payload.envelope_id": {"$nin": envs}}


async def real_policy_filter(safe) -> dict:
    return {"test": {"$ne": True}, "client_id": {"$nin": await test_client_ids(safe)}}
