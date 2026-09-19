"""brAIn provider abstractions.

Phase 3 ships only `MockProvider` (deterministic) and
`AnthropicProviderStub` (raises NotImplementedError). No real network
call reaches an LLM in phase 3.
"""
from app.kernel.providers.anthropic_stub import AnthropicProviderStub
from app.kernel.providers.base import AgentProvider
from app.kernel.providers.mock import MockProvider

__all__ = ["AgentProvider", "AnthropicProviderStub", "MockProvider"]
