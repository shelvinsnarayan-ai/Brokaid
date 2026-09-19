"""AgentProvider Protocol — the seam brAIn uses to reach agents."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.kernel.contracts.models import ContextEnvelope, OutputContract


@runtime_checkable
class AgentProvider(Protocol):
    """Every provider (mock or real) implements this."""

    agent_name: str

    async def run(self, envelope: ContextEnvelope) -> OutputContract:  # pragma: no cover - protocol
        ...
