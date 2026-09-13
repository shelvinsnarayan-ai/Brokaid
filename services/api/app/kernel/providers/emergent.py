"""Emergent LLM provider — thin wrapper over emergentintegrations.

Only used by phase-8+ agents. Each agent has its own subclass (see
`agents/intake.py`) that ships its own system prompt + tool schema +
class allow-list.
"""
from __future__ import annotations

from app.kernel.agents.intake import (
    EmergentLLMProviderIntake as EmergentLLMProviderIntake,
    INTAKE_MODEL as INTAKE_MODEL,
)


__all__ = ["EmergentLLMProviderIntake", "INTAKE_MODEL"]
