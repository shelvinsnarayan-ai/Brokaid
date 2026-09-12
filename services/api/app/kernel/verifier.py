"""In-process chain verifier — matches packages/kernel/verifier/verify.js.

Used by the nightly chain-verify job and by tests. Accepts either a
raw event list or an export JSON dict.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.kernel.canonicalize import event_hash, jcs

GENESIS_PREV = "0" * 64


@dataclass
class VerifyResult:
    ok: bool
    n_events: int
    root_hash: str | None
    failure_seq: int | None = None
    failure_event_id: str | None = None
    reason: str | None = None


def verify_events(events: list[dict[str, Any]]) -> VerifyResult:
    """Recompute the chain end-to-end. First event must have
    prev_hash = 0*64 (genesis). Every subsequent event must chain
    to the previous event's event_hash. Every event_hash must equal
    sha256(canonical material)."""
    if not events:
        return VerifyResult(ok=True, n_events=0, root_hash=None)

    prev = GENESIS_PREV
    root: str | None = None

    for ev in events:
        seq = ev.get("seq")
        # 1. prev_hash chain link
        if ev.get("prev_hash") != prev:
            return VerifyResult(
                ok=False, n_events=len(events), root_hash=None,
                failure_seq=seq, failure_event_id=ev.get("event_id"),
                reason=f"prev_hash mismatch: expected {prev} got {ev.get('prev_hash')}",
            )
        # 2. payload canonicalisation determinism
        expected_payload_jcs = jcs(ev.get("payload"))
        if ev.get("payload_jcs") != expected_payload_jcs:
            return VerifyResult(
                ok=False, n_events=len(events), root_hash=None,
                failure_seq=seq, failure_event_id=ev.get("event_id"),
                reason="payload_jcs mismatch (canonicalisation non-deterministic or payload tampered)",
            )
        # 3. event_hash recomputation
        recomputed = event_hash(
            event_id=ev["event_id"], ts_iso=ev["ts"],
            tenant_id=ev["tenant_id"], actor=ev["actor"],
            type=ev["type"], payload_jcs=ev["payload_jcs"],
            prev_hash=ev["prev_hash"],
        )
        if recomputed != ev.get("event_hash"):
            return VerifyResult(
                ok=False, n_events=len(events), root_hash=None,
                failure_seq=seq, failure_event_id=ev.get("event_id"),
                reason=f"event_hash mismatch: expected {recomputed} got {ev.get('event_hash')}",
            )
        prev = ev["event_hash"]
        root = prev

    return VerifyResult(ok=True, n_events=len(events), root_hash=root)


def verify_export(export: dict[str, Any]) -> VerifyResult:
    events = export.get("events") or []
    return verify_events(events)
