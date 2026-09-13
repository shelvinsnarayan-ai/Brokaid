"""Agent → Provider registry.

Phase 3: every agent name resolves to a fresh MockProvider instance.
Phase 8: `intake` now resolves to the live EmergentLLMProviderIntake
(claude-sonnet-4-6). Every other agent stays on MockProvider until
its phase lands.
"""
from __future__ import annotations

from typing import Callable

from app.kernel.providers.anthropic_stub import AnthropicProviderStub
from app.kernel.providers.base import AgentProvider
from app.kernel.agents.claims_stub import DeterministicClaimsStubProvider
from app.kernel.agents.compliance import EmergentLLMProviderCompliance
from app.kernel.agents.liaison import EmergentLLMProviderLiaison
from app.kernel.agents.placement import EmergentLLMProviderPlacement
from app.kernel.providers.emergent import EmergentLLMProviderIntake
from app.kernel.providers.mock import MockProvider


# Factory type — call it to get a fresh provider instance per invocation.
ProviderFactory = Callable[[], AgentProvider]


def _mock_factory(agent_name: str) -> ProviderFactory:
    def _make() -> AgentProvider:
        return MockProvider(agent_name=agent_name)
    return _make


def _intake_factory() -> AgentProvider:
    return EmergentLLMProviderIntake()


def _placement_factory() -> AgentProvider:
    return EmergentLLMProviderPlacement()


def _compliance_factory() -> AgentProvider:
    return EmergentLLMProviderCompliance()


def _liaison_factory() -> AgentProvider:
    return EmergentLLMProviderLiaison()


AGENT_PROVIDERS: dict[str, ProviderFactory] = {
    "mock": _mock_factory("mock"),
    "intake": _intake_factory,           # phase 8 — LIVE
    "placement": _placement_factory,     # phase 9 — LIVE
    "claims": DeterministicClaimsStubProvider,   # phase 13 — deterministic stub
    "compliance": _compliance_factory,   # phase 10 — LIVE
    "liaison": _liaison_factory,         # phase 12 — LIVE
    "docs": _mock_factory("docs"),
    "notify": _mock_factory("notify"),
    "certs": _mock_factory("certs"),
    "retention": _mock_factory("retention"),
    "finance": _mock_factory("finance"),
    "terrain": _mock_factory("terrain"),
    "campaign": _mock_factory("campaign"),
    # Explicit stub registration so the "no LLM path in phase 3" invariant
    # can be tested by swapping this in place of any of the above.
    "anthropic-stub": AnthropicProviderStub,
}


def resolve(agent_name: str) -> AgentProvider:
    """Return a fresh provider for `agent_name`."""
    if agent_name not in AGENT_PROVIDERS:
        raise KeyError(f"unknown agent: {agent_name}")
    factory = AGENT_PROVIDERS[agent_name]
    return factory()
