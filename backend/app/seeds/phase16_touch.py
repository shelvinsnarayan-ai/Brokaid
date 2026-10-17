"""Phase 16 — one-shot: fresh AgentInvocationCompleted events per agent.

Runs Brain.invoke() against the live preview pool with a small
mock provider so 4 recent ledger events carry `attempt_count` +
`acceptance_mode` in their payload.

Zero LLM tokens — the mock provider never calls the network.
Idempotency-keyed per (envelope_id, agent_name), so re-running is safe.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone


from app.kernel.brain import Brain
from app.kernel.contracts import Actor, ContextEnvelope, EventRef, InvocationBudget
from app.kernel.contracts.models import OutputContract, TokenCount
from app.kernel.trail import create_tenant


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("brokaid.phase16_touch")


AGENTS = ("intake", "placement", "compliance", "liaison")


class _MockProvider:
    """Deterministic OK provider. No LLM."""
    def __init__(self, name: str):
        self.agent_name = name

    async def run(self, envelope: ContextEnvelope) -> OutputContract:
        return OutputContract(
            envelope_id=envelope.envelope_id,
            class_key=envelope.class_key,
            decision="propose",
            payload={"phase16_touch": True, "agent": self.agent_name},
            citations=[],
            notes="phase16 touch — no network",
            tokens_used=TokenCount(input=1, output=1),
            wall_ms=1,
            provider_meta={"mocked": True, "model_id": "phase16-touch"},
        )


def _env(tenant_id: str, agent: str) -> ContextEnvelope:
    return ContextEnvelope(
        envelope_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        matter_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        class_key="structure_risk",
        intent=f"phase16 touch: {agent}",
        inputs={"agent": agent},
        context_events=[EventRef(event_id=str(uuid.uuid4()), seq=1)],
        risk_schema_refs=[],
        tenant_config={},
        budget=InvocationBudget(
            max_input_tokens=4000, max_output_tokens=1000, max_wall_ms=5000,
        ),
        caller=Actor(kind="system", id="phase16-touch", name="phase16-touch"),
        ts=datetime.now(timezone.utc),
    )


async def _main() -> int:
    # TODO(R6): remove — dead since R5c-4a PG removal. The `await None`
    # below is syntactically broken; nothing invokes this entrypoint.
    # Left as an F841 canary + explicit marker for the next cleanup pass.
    dsn = os.environ["MONGO_URL"]  # noqa: F841
    pool = await None  # PG deleted R5c-4a(dsn=dsn, min_size=1, max_size=4)
    try:
        # Use burmac tenant if it exists; else create a scratch one.
        async with pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT tenant_id::text AS id FROM tenants "
                "WHERE slug='burmac' LIMIT 1"
            )
        if row is None:
            tenant, _ = await create_tenant(pool, "burmac", "Burmac")
            tenant_id = tenant.tenant_id
        else:
            tenant_id = row["id"]

        for name in AGENTS:
            provider = _MockProvider(name)
            brain = Brain(pool, resolver=lambda _n, _p=provider: _p)
            out = await brain.invoke(_env(tenant_id, name), name)
            logger.info(
                "invoked %s → envelope=%s payload=%s",
                name, out.envelope_id, out.payload,
            )
    finally:
        await pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
