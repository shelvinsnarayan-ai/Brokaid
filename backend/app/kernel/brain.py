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

Phase R5c-1a: dispatch-aware. `Brain(store, ...)` accepts either an
pool or a Motor DB / TenantSafeDatabase. All 4 trail appends
(AgentInvocationStarted, AgentOutputProduced, SprintCapAlert,
AgentInvocationCompleted, AgentInvocationBudgetBreached) flow through
`trail_dispatch.append_via_dispatch` under `use_tenant(tid)` on
Mongo. `_read_sprint` dispatches to `brain_mongo.read_sprint` on
Mongo (proj_sprint_usage lives in Mongo since R4c). The `pool`
attribute is kept as a backward-compat alias for pre-rewire callers
that read `brain.pool` directly.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Callable

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.tenant_safe import TenantSafeDatabase
from app.kernel.contracts.models import ContextEnvelope, OutputContract
from app.kernel import spend
from app.kernel.gate.confidence import Confidence, compute
from app.kernel.providers import registry as provider_registry
from app.kernel.providers.base import AgentProvider
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch

logger = logging.getLogger("brokaid.brain")


# Phase 16 — transient-error retry policy. Default cap is 2 attempts
# (i.e. 1 retry). BROKAID_ACCEPTANCE_MODE=true caps at 1 attempt
# (no retry). Never retry on 4xx, contract violations, or gAIte blocks.
_TRANSIENT_EXC_TYPES: tuple[type[BaseException], ...] = (
    asyncio.TimeoutError,
    ConnectionError,
    ConnectionResetError,
    OSError,
)


def _retry_cap_from_env() -> int:
    if os.environ.get("BROKAID_ACCEPTANCE_MODE", "").lower() == "true":
        return 1
    raw = os.environ.get("BROKAID_INVOKE_MAX_ATTEMPTS", "2")
    try:
        n = int(raw)
    except ValueError:
        n = 2
    return max(1, n)


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, _TRANSIENT_EXC_TYPES):
        return True
    # httpx / SDK style: any exception whose class name suggests a
    # transient network / 5xx condition is retried.
    name = type(exc).__name__.lower()
    if "timeout" in name or "connect" in name:
        return True
    # Look for HTTP status codes attached to the exception.
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int) and 500 <= status < 600:
        return True
    return False



ProviderResolver = Callable[[str], AgentProvider]


class BrainError(Exception):
    """Base for brAIn-raised control-flow signals."""


class SprintCapBreached(BrainError):
    """Cap already reached before this invocation could start."""


class PerInvocationBudgetBreached(BrainError):
    """Provider blew past `budget.max_output_tokens` or `budget.max_wall_ms`."""


class SpendCapBreached(BrainError):
    """SPEND_CAP_USD reached (estimate from advertised rates)."""


@dataclass
class BrainSettings:
    sprint_cap_tokens: int | None = None
    sprint_cap_tier_thresholds: tuple[int, ...] = (50, 80, 100)


def _is_mongo(store) -> bool:
    return isinstance(store, (AsyncIOMotorDatabase, TenantSafeDatabase))


class Brain:
    """The orchestrator.

    Parameters
    ----------
    store
        Either an `object` (Postgres) or a Motor DB /
        `TenantSafeDatabase` (Mongo). All ledger appends dispatch by
        store type. `brain.pool` is kept as a backward-compat alias
        for pre-rewire callers that read the attribute directly.
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
        store,
        *,
        resolver: ProviderResolver | None = None,
        settings: BrainSettings | None = None,
    ):
        self.store = store
        # Backward-compat: pre-rewire callers may read `brain.pool`.
        self.pool = store
        self.resolver = resolver or provider_registry.resolve
        self.settings = settings or BrainSettings()

    # ---- append helper (dispatch-aware) ---------------------------------

    async def _emit(self, tenant_id: str, **kw):
        """Return the {event_id, seq, event_hash} dict from dispatch."""
        if _is_mongo(self.store):
            async with use_tenant(tenant_id):
                return await append_via_dispatch(
                    self.store, tenant_id, **kw,
                )
        return await append_via_dispatch(self.store, tenant_id, **kw)

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
        await self._emit(
            tenant_id, type="AgentInvocationStarted",
            actor=self.ACTOR,
            payload={
                "envelope_id": envelope_id,
                "agent_name": agent_name,
                "class_key": envelope.class_key,
                "budget": envelope.budget.model_dump(),
            },
            idempotency_key=f"aginv:start:{envelope_id}:{agent_name}",
        )

        # a2. Kill switch (R0.5) — no model call when a switch matches --
        if _is_mongo(self.store):
            from app.kernel import governance
            ks = await governance.check(self.store, agent_name, tenant_id)
            if ks is not None:
                blocked = governance.KillSwitchActive(ks, agent_name)
                await self._emit(
                    tenant_id, type="AgentInvocationBlocked", actor=self.ACTOR,
                    payload={"envelope_id": envelope_id, "agent_name": agent_name,
                             **blocked.as_payload()},
                    idempotency_key=f"aginv:blocked:{envelope_id}:{agent_name}",
                )
                await self._emit_completed(tenant_id, envelope_id, agent_name, "blocked")
                raise blocked

        # a3. Spend cap (R0.5, USD estimate) ------------------------------
        if _is_mongo(self.store) and spend.cap_usd() is not None:
            cost_now = (await spend.summary(self.store, tenant_id))["cost_usd"]
            if cost_now >= spend.cap_usd():
                await self._emit_breach(
                    tenant_id, envelope_id, agent_name, "spend_cap",
                    {"cost_usd": cost_now, "cap_usd": spend.cap_usd(), "estimate": True},
                )
                await self._emit_completed(tenant_id, envelope_id, agent_name, "breach")
                raise SpendCapBreached(f"spend cap ${spend.cap_usd()} reached (estimate ${cost_now})")

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

        # d + e. Run with wall_ms enforcement + transient-error retry -
        wall_s = envelope.budget.max_wall_ms / 1000.0
        max_attempts = _retry_cap_from_env()
        attempt = 0
        output: OutputContract | None = None
        while attempt < max_attempts:
            attempt += 1
            try:
                output = await asyncio.wait_for(
                    provider.run(envelope), timeout=wall_s
                )
                break
            except asyncio.TimeoutError:
                if attempt >= max_attempts:
                    await self._emit_breach(
                        tenant_id, envelope_id, agent_name, "wall_ms",
                        {"budget_ms": envelope.budget.max_wall_ms,
                         "attempts": attempt},
                    )
                    await self._emit_completed(
                        tenant_id, envelope_id, agent_name, "breach",
                        attempts=attempt,
                    )
                    raise
                logger.info(
                    "brain: transient timeout on attempt %d/%d for agent=%s",
                    attempt, max_attempts, agent_name,
                )
                continue
            except NotImplementedError:
                await self._emit_completed(
                    tenant_id, envelope_id, agent_name, "error",
                    attempts=attempt,
                )
                raise
            except Exception as e:
                if not _is_transient(e) or attempt >= max_attempts:
                    await self._emit_completed(
                        tenant_id, envelope_id, agent_name, "error",
                        attempts=attempt,
                    )
                    raise
                logger.info(
                    "brain: transient error %s on attempt %d/%d for agent=%s",
                    type(e).__name__, attempt, max_attempts, agent_name,
                )
                continue

        assert output is not None, (
            "brain: retry loop exited without output — should be unreachable"
        )

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
                tenant_id, envelope_id, agent_name, "breach",
                attempts=attempt,
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

        cost = spend.cost_usd(agent_name, output.tokens_used.input, output.tokens_used.output)

        # h. AgentOutputProduced -----------------------------------------
        # This event's projection updates proj_sprint_usage atomically.
        await self._emit(
            tenant_id, type="AgentOutputProduced",
            actor=self.ACTOR,
            payload={
                "envelope_id": envelope_id,
                "agent_name": agent_name,
                "class_key": output.class_key,
                "decision": output.decision,
                "citations": [c.model_dump() for c in output.citations],
                "tokens_used": output.tokens_used.model_dump(),
                "cost_usd": cost,
                "model": spend.AGENT_MODEL.get(agent_name, "mock"),
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
                    await self._emit(
                        tenant_id, type="SprintCapAlert",
                        actor=self.ACTOR,
                        payload={
                            "threshold_pct": pct,
                            "current_tokens": new_total,
                            "cap": cap,
                        },
                        idempotency_key=f"aginv:alert:{tenant_id}:{pct}",
                    )
                    highest_pct_crossed = pct

        if _is_mongo(self.store):
            total = (await spend.summary(self.store, tenant_id))["cost_usd"]
            await spend.record_alerts(self.store, tenant_id, total, self._emit)

        # j. AgentInvocationCompleted ------------------------------------
        await self._emit_completed(
            tenant_id, envelope_id, agent_name, "ok",
            attempts=attempt, cost_usd=cost,
        )

        return output

    # ---- helpers ---------------------------------------------------------

    async def _read_sprint(self, tenant_id: str) -> tuple[int, int]:
        if _is_mongo(self.store):
            from app.kernel import brain_mongo
            return await brain_mongo.read_sprint(self.store, tenant_id)
        async with self.store.acquire() as c:
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
        self, tenant_id: str, envelope_id: str, agent_name: str, outcome: str,
        *, attempts: int = 1, cost_usd: float = 0.0,
    ) -> None:
        await self._emit(
            tenant_id, type="AgentInvocationCompleted",
            actor=self.ACTOR,
            payload={
                "envelope_id": envelope_id,
                "agent_name": agent_name,
                "outcome": outcome,
                "attempt_count": attempts,
                "cost_usd": cost_usd,
                "cost_estimate": True,
                "acceptance_mode": (
                    os.environ.get("BROKAID_ACCEPTANCE_MODE", "").lower()
                    == "true"
                ),
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
        await self._emit(
            tenant_id, type="AgentInvocationBudgetBreached",
            actor=self.ACTOR,
            payload={
                "envelope_id": envelope_id,
                "agent_name": agent_name,
                "reason": reason,
                "detail": detail,
            },
            idempotency_key=f"aginv:breach:{envelope_id}:{reason}",
        )
