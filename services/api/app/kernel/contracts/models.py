"""Pydantic v2 contracts for brAIn — ContextEnvelope, OutputContract, Approval.

SCHEMA_VERIFICATION_PENDING: Field names based on plan v1.3.1;
awaiting verbatim §7.5-7.7 confirmation from spec owner.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---- shared primitives -----------------------------------------------------


class Actor(BaseModel):
    """Who initiated / performed something. Matches trAIl's actor shape."""
    model_config = ConfigDict(extra="forbid")

    kind: Literal["system", "human", "agent"]
    id: str
    name: str


class EventRef(BaseModel):
    """Pointer into the tenant's ledger."""
    model_config = ConfigDict(extra="forbid")

    event_id: str
    seq: int | None = None


class Citation(BaseModel):
    """A citation the agent attaches to a claim in its output."""
    model_config = ConfigDict(extra="forbid")

    kind: Literal["event", "risk_schema"]
    ref: str


class InvocationBudget(BaseModel):
    """Per-invocation resource cap for brAIn."""
    model_config = ConfigDict(extra="forbid")

    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    max_wall_ms: int = Field(gt=0)


class TokenCount(BaseModel):
    """Provider-reported token consumption, verified by brAIn."""
    model_config = ConfigDict(extra="forbid")

    input: int = Field(ge=0)
    output: int = Field(ge=0)


# ---- ContextEnvelope -------------------------------------------------------


class ContextEnvelope(BaseModel):
    """The input every agent gets.

    SCHEMA_VERIFICATION_PENDING: Field names based on plan v1.3.1;
    awaiting verbatim §7.5-7.7 confirmation from spec owner.
    """
    model_config = ConfigDict(extra="forbid")

    envelope_id: UUID
    tenant_id: UUID
    matter_id: UUID | None = None
    client_id: UUID | None = None
    class_key: str
    intent: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    context_events: list[EventRef] = Field(default_factory=list)
    risk_schema_refs: list[str] = Field(default_factory=list)
    tenant_config: dict[str, Any] = Field(default_factory=dict)
    budget: InvocationBudget
    caller: Actor
    ts: datetime

    @field_validator("class_key")
    @classmethod
    def _known_class(cls, v: str) -> str:
        # Late import to avoid a circular dep between contracts and gate.
        from app.kernel.gate.classes import CLASSES

        if v not in CLASSES:
            raise ValueError(f"unknown class_key: {v}")
        return v


# ---- OutputContract --------------------------------------------------------


class OutputContract(BaseModel):
    """What every agent returns.

    SCHEMA_VERIFICATION_PENDING: Field names based on plan v1.3.1;
    awaiting verbatim §7.5-7.7 confirmation from spec owner.
    """
    model_config = ConfigDict(extra="forbid")

    envelope_id: UUID
    class_key: str
    decision: str
    payload: dict[str, Any] = Field(default_factory=dict)
    citations: list[Citation] = Field(default_factory=list)
    notes: str | None = None
    tokens_used: TokenCount
    wall_ms: int = Field(ge=0)
    # provider_meta is opaque; brAIn never renders it to any surface.
    provider_meta: dict[str, Any] = Field(default_factory=dict)


# ---- Approval --------------------------------------------------------------


class Approval(BaseModel):
    """Operator decision on a HITL card.

    SCHEMA_VERIFICATION_PENDING: Field names based on plan v1.3.1;
    awaiting verbatim §7.5-7.7 confirmation from spec owner.
    """
    model_config = ConfigDict(extra="forbid")

    envelope_id: UUID
    approver: Actor
    decision: Literal["approve", "edit", "reject", "block"]
    edits: dict[str, Any] | None = None
    reason: str | None = None
    ts: datetime


# ---- Pair validation -------------------------------------------------------


def validate_output_matches_envelope(
    envelope: ContextEnvelope, output: OutputContract
) -> None:
    """Raise ValueError if the output does not correspond to the envelope.

    Currently checks envelope_id equality. class_key mismatch is NOT an
    error here — the mismatch drives class_agreement in the confidence
    calculation.
    """
    if output.envelope_id != envelope.envelope_id:
        raise ValueError(
            f"output.envelope_id {output.envelope_id} != "
            f"envelope.envelope_id {envelope.envelope_id}"
        )
