"""RFC 8785 JSON Canonicalisation + event-hash construction.

The event hash is:

    sha256(
        event_id + "\n" +
        ts_iso   + "\n" +
        tenant_id + "\n" +
        actor_jcs + "\n" +
        type      + "\n" +
        payload_jcs + "\n" +
        prev_hash
    )

`\n` separators eliminate any field-boundary ambiguity — JCS output
contains no raw newlines, UUIDs contain no newlines, ISO timestamps
contain no newlines, hex hashes contain no newlines. The Node
verifier at `packages/kernel/verifier/verify.js` MUST use the same
construction.
"""
from __future__ import annotations

import hashlib
from typing import Any

import rfc8785


def jcs(obj: Any) -> str:
    """RFC 8785 canonical JSON as a UTF-8 string."""
    return rfc8785.dumps(obj).decode("utf-8")


def event_hash(
    *,
    event_id: str,
    ts_iso: str,
    tenant_id: str,
    actor: dict,
    type: str,
    payload_jcs: str,
    prev_hash: str,
) -> str:
    actor_jcs = jcs(actor)
    material = "\n".join([
        event_id, ts_iso, tenant_id, actor_jcs, type, payload_jcs, prev_hash,
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
