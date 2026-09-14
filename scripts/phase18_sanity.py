"""Phase 18 sanity — run intake through Brain.invoke against the live pool
so an AgentInvocationCompleted event lands with attempt_count populated."""
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "services" / "api"))

import asyncpg  # noqa: E402
from app.kernel.brain import Brain  # noqa: E402
from app.kernel.agents.intake import EmergentLLMProviderIntake  # noqa: E402
from app.kernel.providers import registry  # noqa: E402
from app.kernel.contracts.models import (  # noqa: E402
    Actor, ContextEnvelope, InvocationBudget,
)


BURMAC = "e73e7029-da66-4659-9ac8-845f22b1f8a5"


async def main() -> int:
    registry.AGENT_PROVIDERS["intake"] = lambda: EmergentLLMProviderIntake()
    dsn = "postgresql://brokaid:brokaid_dev_local@127.0.0.1:5432/brokaid"
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)

    # Ensure Burmac tenant + brokerage exist (data dir came back empty).
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO tenants (tenant_id, slug, display_name, "
            "genesis_hash, created_at) "
            "VALUES ($1::uuid, 'burmac', 'Burmac Brokers', "
            " repeat('0', 64), NOW()) "
            "ON CONFLICT (tenant_id) DO NOTHING",
            BURMAC,
        )
        # RLS scope for the reads below.
        await c.execute(
            "SELECT set_config('app.tenant_id', $1, false)", BURMAC
        )

    fx = json.loads((REPO / "services" / "api" / "tests" / "fixtures"
                     / "golden_intake.json").read_text())
    limit_raise = [g for g in fx["goldens"] if g["id"] == "limit_raise"][0]

    envelope = ContextEnvelope(
        envelope_id=uuid.uuid4(),
        tenant_id=uuid.UUID(BURMAC),
        matter_id=None, client_id=None,
        class_key="request_info",
        intent="parse client intake",
        inputs={
            "raw_message": limit_raise["raw_message"],
            "policy_id": None,
            "context_events_summary": "no prior events",
        },
        context_events=[], risk_schema_refs=[], tenant_config={},
        budget=InvocationBudget(
            max_input_tokens=4000, max_output_tokens=1500, max_wall_ms=45000,
        ),
        caller=Actor(kind="system", id="phase18_sanity",
                     name="phase18_sanity"),
        ts=datetime.now(timezone.utc),
    )

    brain = Brain(pool)
    out = await brain.invoke(envelope, "intake")

    print(f"envelope_id:    {envelope.envelope_id}")
    print(f"class_proposed: {out.payload.get('class_proposed')}")
    print(f"tokens:         {out.tokens_used}")

    async with pool.acquire() as c:
        await c.execute(
            "SELECT set_config('app.tenant_id', $1, false)", BURMAC
        )
        row = await c.fetchrow(
            "SELECT event_id::text AS eid, payload "
            "FROM events "
            "WHERE tenant_id=$1::uuid "
            "  AND type='AgentInvocationCompleted' "
            "  AND payload->>'envelope_id'=$2 "
            "ORDER BY seq DESC LIMIT 1",
            BURMAC, str(envelope.envelope_id),
        )
        if row:
            p = row["payload"]
            if isinstance(p, str):
                p = json.loads(p)
            print(f"event_id:       {row['eid']}")
            print(f"attempt_count:  {p.get('attempt_count')}")
            print(f"agent_name:     {p.get('agent_name')}")
            print(f"acceptance_mode:{p.get('acceptance_mode')}")
            print(f"mocked:         {p.get('mocked')}")
        else:
            print("event_id:       NONE (Brain did not persist)")

    await pool.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
