"""Agent → Provider registry.

Phase 3: every agent name resolves to a fresh MockProvider instance.
The stub is registered under an explicit name so the seam is testable
without polluting the default agent map.
"""
from __future__ import annotations

from typing import Callable

from app.kernel.providers.anthropic_stub import AnthropicProviderStub
from app.kernel.providers.base import AgentProvider
from app.kernel.providers.mock import MockProvider


# Factory type — call it to get a fresh provider instance per invocation.
ProviderFactory = Callable[[], AgentProvider]


def _mock_factory(agent_name: str) -> ProviderFactory:
    def _make() -> AgentProvider:
        return MockProvider(agent_name=agent_name)
    return _make


AGENT_PROVIDERS: dict[str, ProviderFactory] = {
    # Placeholder set of agent names — phases 4+ will replace each with
    # its real provider. In phase 3 every one is a MockProvider.
    "mock": _mock_factory("mock"),
    "intake": _mock_factory("intake"),
    "placement": _mock_factory("placement"),
    "claims": _mock_factory("claims"),
    "compliance": _mock_factory("compliance"),
    "liaison": _mock_factory("liaison"),
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
    # Factory can be a class (AnthropicProviderStub) or a callable.
    return factory()
