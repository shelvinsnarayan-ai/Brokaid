"""Phase R5c-2a — Mongo variant of the phase-16 fresh-agent touch.

Mirror of `app.seeds.phase16_touch` — invokes `Brain(mongo_db)` four
times (one per agent: intake / placement / compliance / liaison)
against a deterministic mock provider so the tenant carries four
fresh `AgentInvocationStarted → AgentOutputProduced →
AgentInvocationCompleted` triples in the ledger.

Zero network. Idempotency-keyed on the Brain kernel's own trail
appends (see `kernel/brain.py` — `aginv:start:{envelope_id}:{agent}`
etc.), so re-running is safe.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.tenant_safe import wrap
from app.kernel import trail_mongo
from app.kernel.brain import Brain
from app.kernel.contracts import (
    Actor, ContextEnvelope, EventRef, InvocationBudget,
)
from app.kernel.contracts.models import OutputContract, TokenCount
from app.kernel.tenant_ctx import use_tenant
from app.seeds.phase16_touch import AGENTS
from app.seeds.seed_users import TENANT_NAME, TENANT_SLUG


logger = logging.getLogger("brokaid.phase16_touch_mongo")


class _MockProvider:
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


async def touch_mongo(db: AsyncIOMotorDatabase) -> str:
    """Resolve (or create) the burmac tenant on Mongo, then invoke
    `Brain(mongo_db)` for each of the 4 agents. Returns the
    resolved tenant_id.
    """
    safe = wrap(db)
    # tenant-safe: system-only — pre-context tenant registry lookup.
    row = await safe.system("tenants").find_one({"slug": TENANT_SLUG})
    if row is None:
        new_tid = str(uuid.uuid4())
        async with use_tenant(new_tid):
            tenant, _ = await trail_mongo.create_tenant(
                db, TENANT_SLUG, TENANT_NAME,
                tenant_id_override=new_tid,
            )
        tenant_id = tenant.tenant_id
    else:
        tenant_id = row["tenant_id"]

    for name in AGENTS:
        provider = _MockProvider(name)
        brain = Brain(db, resolver=lambda _n, _p=provider: _p)
        out = await brain.invoke(_env(tenant_id, name), name)
        logger.info(
            "invoked %s → envelope=%s payload=%s",
            name, out.envelope_id, out.payload,
        )
    return tenant_id
