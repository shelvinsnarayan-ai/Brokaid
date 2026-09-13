"""brAIn — the orchestrator that binds an agent proposal through
gAIte into the trAIl.

Phase 3 posture:
  - LLM budget: zero. Every provider on the default registry is a
    MockProvider; `AnthropicProviderStub` raises NotImplementedError.
  - Sprint cap: configurable via env (`SPRINT_CAP_TOKENS`, None = waived).
    Usage is always tracked in `proj_sprint_usage` so a later cap set
    works retroactively.
  - Per-invocation budget is enforced on wall_ms (`asyncio.wait_for`)
    and on tokens_used.output post-hoc. Input token overrun is logged
    and flagged in the AgentOutputProduced payload but does not raise.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

import asyncpg

from app.kernel.contracts.models import ContextEnvelope, OutputContract
from app.kernel.gate.confidence import Confidence, compute
from app.kernel.providers import registry as provider_registry
from app.kernel.providers.base import AgentProvider
from app.kernel.trail import append

logger = logging.getLogger("brokaid.brain")


ProviderResolver = Callable[[str], AgentProvider]


class BrainError(Exception):
    """Base for brAIn-raised control-flow signals."""


class SprintCapBreached(BrainError):
    """Cap already reached before this invocation could start."""


class PerInvocationBudgetBreached(BrainError):
    """Provider blew past `budget.max_output_tokens` or `budget.max_wall_ms`."""


@dataclass
class BrainSettings:
    sprint_cap_tokens: int | None = None
    sprint_cap_tier_thresholds: tuple[int, ...] = (50, 80, 100)


class Brain:
    """The orchestrator.

    Parameters
    ----------
    pool
        asyncpg pool wired to the tenant Postgres.
    resolver
        Callable ``str -> AgentProvider``. Defaults to
        `provider_registry.resolve`. Injected in tests.
    settings
        Optional `BrainSettings`. Falls back to reading env at
        construction time via `BrainSettings.from_env()`.
    """

    ACTOR = {"kind": "system", "id": "brain", "name": "brain"}

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        resolver: ProviderResolver | None = None,
        settings: BrainSettings | None = None,
    ):
        self.pool = pool
        self.resolver = resolver or provider_registry.resolve
        self.settings = settings or BrainSettings()

    # ---- public entry -----------------------------------------------------

    async def invoke(
        self, envelope: ContextEnvelope, agent_name: str
    ) -> OutputContract:
        """Run one agent invocation end-to-end and return its output.

        Raises
        ------
        SprintCapBreached
            If the tenant's sprint total already met/exceeded the cap.
        PerInvocationBudgetBreached
            If the provider exceeded budget.max_output_tokens.
        asyncio.TimeoutError
            If the provider exceeded budget.max_wall_ms.
        Any provider exception
            Propagated after emitting AgentInvocationCompleted{outcome='error'}.
        """
        tenant_id = str(envelope.tenant_id)
        envelope_id = str(envelope.envelope_id)

        # a. AgentInvocationStarted --------------------------------------
        await append(
            self.pool, tenant_id, "AgentInvocationStarted",
            actor=self.ACTOR,
            payload={
                "envelope_id": envelope_id,
                "agent_name": agent_name,
                "class_key": envelope.class_key,
                "budget": envelope.budget.model_dump(),
            },
            idempotency_key=f"aginv:start:{envelope_id}:{agent_name}",
        )

        # b. Sprint cap check --------------------------------------------
        cap = self.settings.sprint_cap_tokens
        current, last_alert_pct = await self._read_sprint(tenant_id)
        if cap is not None and current >= cap:
            await self._emit_breach(
                tenant_id, envelope_id, agent_name, "sprint_cap",
                {"current": current, "cap": cap},
            )
            await self._emit_completed(
                tenant_id, envelope_id, agent_name, "breach"
            )
            raise SprintCapBreached(f"sprint cap {cap} exceeded (current={current})")

        # c. Resolve provider --------------------------------------------
        provider = self.resolver(agent_name)

        # d + e. Run with wall_ms enforcement ----------------------------
        wall_s = envelope.budget.max_wall_ms / 1000.0
        try:
            output: OutputContract = await asyncio.wait_for(
                provider.run(envelope), timeout=wall_s
            )
        except asyncio.TimeoutError:
            await self._emit_breach(
                tenant_id, envelope_id, agent_name, "wall_ms",
                {"budget_ms": envelope.budget.max_wall_ms},
            )
            await self._emit_completed(
                tenant_id, envelope_id, agent_name, "breach"
            )
            raise
        except NotImplementedError:
            # Provider seam intentionally not wired (AnthropicProviderStub
            # in phase 3). Emit error outcome and re-raise.
            await self._emit_completed(
                tenant_id, envelope_id, agent_name, "error"
            )
            raise
        except Exception:
            await self._emit_completed(
                tenant_id, envelope_id, agent_name, "error"
            )
            raise

        # f. Post-check output tokens ------------------------------------
        input_overrun = (
            output.tokens_used.input > envelope.budget.max_input_tokens
        )
        if output.tokens_used.output > envelope.budget.max_output_tokens:
            await self._emit_breach(
                tenant_id, envelope_id, agent_name, "per_invocation_output",
                {
                    "budget_output_tokens": envelope.budget.max_output_tokens,
                    "used_output_tokens": output.tokens_used.output,
                },
            )
            await self._emit_completed(
                tenant_id, envelope_id, agent_name, "breach"
            )
            raise PerInvocationBudgetBreached(
                f"output tokens {output.tokens_used.output} > "
                f"budget {envelope.budget.max_output_tokens}"
            )
        if input_overrun:
            logger.warning(
                "brain: input tokens overrun (used=%s, budget=%s) — "
                "output still emitted with flag",
                output.tokens_used.input, envelope.budget.max_input_tokens,
            )

        # g. Compute confidence deterministically ------------------------
        schema_valid = (
            output.class_key == envelope.class_key and bool(output.payload)
        )
        claims: list[dict] | None = None
        if output.citations or output.class_key == envelope.class_key:
            # A single implicit claim represented by the output itself.
            claims = [{
                "citations": [c.model_dump() for c in output.citations],
            }]
        confidence: Confidence = compute(
            payload=output.payload,
            required_fields=[],
            schema_valid=schema_valid,
            claims=claims,
            proposed_class=output.class_key,
            resolved_class=envelope.class_key,
        )

        # h. AgentOutputProduced -----------------------------------------
        # This event's projection updates proj_sprint_usage atomically.
        await append(
            self.pool, tenant_id, "AgentOutputProduced",
            actor=self.ACTOR,
            payload={
                "envelope_id": envelope_id,
                "agent_name": agent_name,
                "class_key": output.class_key,
                "decision": output.decision,
                "citations": [c.model_dump() for c in output.citations],
                "tokens_used": output.tokens_used.model_dump(),
                "wall_ms": output.wall_ms,
                "confidence": {
                    "components": dict(confidence.components),
                    "scalar": confidence.scalar,
                },
                "flags": {"input_tokens_overrun": input_overrun},
            },
            idempotency_key=f"aginv:output:{envelope_id}:{agent_name}",
        )

        # i. Sprint cap alerts -------------------------------------------
        new_total = current + output.tokens_used.input + output.tokens_used.output
        if cap is not None:
            highest_pct_crossed = last_alert_pct
            for pct in self.settings.sprint_cap_tier_thresholds:
                threshold = (cap * pct) // 100
                if new_total >= threshold and pct > highest_pct_crossed:
                    await append(
                        self.pool, tenant_id, "SprintCapAlert",
                        actor=self.ACTOR,
                        payload={
                            "threshold_pct": pct,
                            "current_tokens": new_total,
                            "cap": cap,
                        },
                        idempotency_key=f"aginv:alert:{tenant_id}:{pct}",
                    )
                    highest_pct_crossed = pct

        # j. AgentInvocationCompleted ------------------------------------
        await self._emit_completed(
            tenant_id, envelope_id, agent_name, "ok"
        )

        return output

    # ---- helpers ---------------------------------------------------------

    async def _read_sprint(self, tenant_id: str) -> tuple[int, int]:
        async with self.pool.acquire() as c:
            async with c.transaction():
                await c.execute(
                    "SELECT set_config('app.tenant_id', $1, true)", tenant_id
                )
                row = await c.fetchrow(
                    "SELECT tokens_used, last_alert_pct FROM proj_sprint_usage "
                    "WHERE tenant_id = $1::uuid",
                    tenant_id,
                )
        if row is None:
            return 0, 0
        return int(row["tokens_used"]), int(row["last_alert_pct"])

    async def _emit_completed(
        self, tenant_id: str, envelope_id: str, agent_name: str, outcome: str
    ) -> None:
        await append(
            self.pool, tenant_id, "AgentInvocationCompleted",
            actor=self.ACTOR,
            payload={
                "envelope_id": envelope_id,
                "agent_name": agent_name,
                "outcome": outcome,
            },
            idempotency_key=f"aginv:done:{envelope_id}:{agent_name}",
        )

    async def _emit_breach(
        self,
        tenant_id: str,
        envelope_id: str,
        agent_name: str,
        reason: str,
        detail: dict,
    ) -> None:
        await append(
            self.pool, tenant_id, "AgentInvocationBudgetBreached",
            actor=self.ACTOR,
            payload={
                "envelope_id": envelope_id,
                "agent_name": agent_name,
                "reason": reason,
                "detail": detail,
            },
            idempotency_key=f"aginv:breach:{envelope_id}:{reason}",
        )
