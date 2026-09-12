"""Deterministic mock provider for phase 3.

Behaviour modes:
  - success:                 well-formed OutputContract, tokens=(input,output).
  - overrun_output_tokens:   tokens_used.output > budget.max_output_tokens.
  - overrun_wall_ms:         sleeps longer than budget.max_wall_ms.
  - invalid_schema:          empty payload + mismatched class_key.
  - bad_citation:            citations list references an unknown event id.

Never hits a network. `provider_meta.mocked = True` is always set.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.kernel.contracts.models import (
    Citation,
    ContextEnvelope,
    OutputContract,
    TokenCount,
)


@dataclass
class MockProvider:
    agent_name: str = "mock"
    mode: str = "success"
    tokens_per_call: tuple[int, int] = (5, 5)
    canned_decision: str = "propose-continue"
    canned_payload: dict = field(default_factory=lambda: {"note": "mock-ok"})

    async def run(self, envelope: ContextEnvelope) -> OutputContract:
        mode = self.mode
        input_tokens, output_tokens = self.tokens_per_call

        if mode == "overrun_wall_ms":
            # Sleep long enough to exceed the budget; brAIn's
            # asyncio.wait_for will cancel us.
            await asyncio.sleep((envelope.budget.max_wall_ms / 1000.0) + 0.5)

        if mode == "overrun_output_tokens":
            output_tokens = envelope.budget.max_output_tokens + 50

        if mode == "invalid_schema":
            return OutputContract(
                envelope_id=envelope.envelope_id,
                class_key="_invalid_class_",   # deliberate mismatch
                decision=self.canned_decision,
                payload={},                    # deliberately empty
                citations=[],
                notes=None,
                tokens_used=TokenCount(input=input_tokens, output=output_tokens),
                wall_ms=1,
                provider_meta={"mocked": True, "mode": mode},
            )

        citations: list[Citation] = []
        if mode == "bad_citation":
            citations = [Citation(kind="event", ref="00000000-not-a-real-event")]
        elif envelope.context_events:
            # Cite the first context event so citation_existence = 1.
            citations = [
                Citation(kind="event", ref=envelope.context_events[0].event_id)
            ]

        return OutputContract(
            envelope_id=envelope.envelope_id,
            class_key=envelope.class_key,
            decision=self.canned_decision,
            payload=self.canned_payload,
            citations=citations,
            notes=f"mock:{mode}",
            tokens_used=TokenCount(input=input_tokens, output=output_tokens),
            wall_ms=1,
            provider_meta={"mocked": True, "mode": mode},
        )
