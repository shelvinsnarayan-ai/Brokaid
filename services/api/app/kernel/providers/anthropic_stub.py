"""AnthropicProviderStub — the interface is real, the network path is not.

Any invocation of `.run()` raises NotImplementedError. This proves the
seam exists without any risk of an accidental production LLM call in
phase 3.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.kernel.contracts.models import ContextEnvelope, OutputContract


@dataclass
class AnthropicProviderStub:
    agent_name: str = "anthropic-stub"

    async def run(self, envelope: ContextEnvelope) -> OutputContract:  # noqa: ARG002
        raise NotImplementedError(
            "AnthropicProviderStub is a phase-3 seam; real provider lands "
            "in phase 4+. This is intentional — no LLM path exists in "
            "phase 3."
        )
