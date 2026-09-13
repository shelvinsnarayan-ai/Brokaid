"""intAIke — the intake agent (first live LLM path).

Class routing:
    Only fires for intake-eligible class_keys. Other classes → the
    provider raises AgentClassMismatch and brAIn's error path fires.

Model pin:
    Model ID is a runtime constant pinned in this module and echoed
    into the AgentInvocationStarted ledger event. It never appears
    on any operator or client response surface (scrubbed by
    `strip_model_id` before rendering).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from emergentintegrations.llm.chat import LlmChat, UserMessage

from app.kernel.contracts.models import (
    Citation,
    ContextEnvelope,
    OutputContract,
    TokenCount,
)


# ---- Pinned model (never on screen) ----------------------------------------
INTAKE_MODEL = "claude-sonnet-4-6"
INTAKE_PROVIDER = "anthropic"

# ---- Class allow-list for the intake agent ---------------------------------
INTAKE_ELIGIBLE_CLASSES = frozenset({
    "request_info",
    "endorsement_execute_instructed_quoted",
    "submit_to_market",
    "claim_admin",
    "certificate_issue",
    "renewal_pack_unchanged",
    "comms_advisory",         # coverage-position questions
    "bind_new_or_remarket",   # new cover bindable in-house
})


class AgentClassMismatch(Exception):
    """intAIke was asked to run on a class it does not handle."""


# ---- Tool schema for the model to fill in ----------------------------------
_CLASS_ENUM = sorted(INTAKE_ELIGIBLE_CLASSES)
_OPS = ["increase", "decrease", "set", "add", "remove"]
_ADVICE = ["factual", "opinion", "recommendation"]

INTAKE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "record_intake_proposal",
        "description": (
            "Record the parsed client intake as a structured Matter update "
            "proposal. This is the ONLY output surface — do not write prose."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "parsed_intent": {"type": "string"},
                "class_proposed": {"type": "string", "enum": _CLASS_ENUM},
                "target_policy_id": {"type": ["string", "null"]},
                "proposed_changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "field_path": {"type": "string"},
                            "operation": {"type": "string", "enum": _OPS},
                            "from_value": {},
                            "to_value": {},
                            "citation": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "kind": {"type": "string", "enum": ["policy_schedule"]},
                                    "policy_id": {"type": ["string", "null"]},
                                    "field_path": {"type": "string"},
                                },
                                "required": ["kind", "field_path"],
                            },
                        },
                        "required": ["field_path", "operation", "to_value", "citation"],
                    },
                },
                "requires_information": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "advice_classification": {"type": "string", "enum": _ADVICE},
                "notes": {"type": "string"},
            },
            "required": [
                "parsed_intent", "class_proposed", "proposed_changes",
                "requires_information", "advice_classification", "notes",
            ],
        },
    },
}


def _load_system_prompt() -> str:
    p = Path(__file__).parent / "prompts" / "intake.md"
    return p.read_text(encoding="utf-8")


SYSTEM_PROMPT = _load_system_prompt()


def _user_message_text(envelope: ContextEnvelope) -> str:
    """Compose the model input from envelope.inputs (never real client data)."""
    inputs = envelope.inputs or {}
    raw = inputs.get("raw_message") or ""
    ctx = inputs.get("context_events_summary") or "(no prior context)"
    policy_id = inputs.get("policy_id")
    return (
        "CLIENT MESSAGE:\n"
        f"{raw.strip()}\n\n"
        "CONTEXT (last N ledger events, summarised):\n"
        f"{ctx}\n\n"
        f"KNOWN POLICY ID (may be null): {policy_id}\n\n"
        "Return ONE call to the record_intake_proposal tool. No prose."
    )


class EmergentLLMProviderIntake:
    """Concrete provider for the intake agent.

    Implements the AgentProvider Protocol (has `agent_name` attribute and
    an async `run(envelope) -> OutputContract`). Registered under
    "intake" in the provider registry.
    """

    agent_name = "intake"

    def __init__(self, *, api_key: str | None = None, model: str = INTAKE_MODEL):
        self._api_key = api_key or os.environ.get("EMERGENT_LLM_KEY")
        self._model = model

    async def run(self, envelope: ContextEnvelope) -> OutputContract:
        if envelope.class_key not in INTAKE_ELIGIBLE_CLASSES:
            raise AgentClassMismatch(
                f"intake does not handle class {envelope.class_key!r}"
            )
        if not self._api_key:
            raise RuntimeError(
                "EMERGENT_LLM_KEY is not configured; intake requires a live provider."
            )

        session_id = f"intake-{envelope.envelope_id}"
        chat = (
            LlmChat(
                api_key=self._api_key,
                session_id=session_id,
                system_message=SYSTEM_PROMPT,
            )
            .with_model(INTAKE_PROVIDER, self._model)
            .with_tools([INTAKE_TOOL], tool_choice="required")
        )

        start = time.monotonic()
        response = await chat.send_message_with_tools(
            UserMessage(text=_user_message_text(envelope))
        )
        wall_ms = int((time.monotonic() - start) * 1000)

        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            # Model refused to call the tool — schema_validity will be 0.
            return _empty_output(envelope, wall_ms, response, self._model,
                                 reason="no_tool_call")

        tc = tool_calls[0]
        try:
            args = tc.arguments if isinstance(tc.arguments, dict) else json.loads(tc.arguments)
        except (TypeError, ValueError) as e:
            return _empty_output(envelope, wall_ms, response, self._model,
                                 reason=f"bad_json: {e!s}")

        # Post-check: every proposed_change must carry a citation the model
        # actually rendered (belt-and-braces alongside the tool schema).
        changes = args.get("proposed_changes") or []
        for i, ch in enumerate(changes):
            if not (ch.get("citation") and ch["citation"].get("field_path")):
                return _empty_output(envelope, wall_ms, response, self._model,
                                     reason=f"uncited_change_{i}")

        citations: list[Citation] = []
        seen_fields: set[str] = set()
        for ch in changes:
            fp = ch["citation"]["field_path"]
            if fp not in seen_fields:
                seen_fields.add(fp)
                citations.append(Citation(kind="risk_schema", ref=fp))
        # Cite the raw_message event too, if provided.
        raw_ref = (envelope.inputs or {}).get("raw_message_event_id")
        if raw_ref:
            citations.append(Citation(kind="event", ref=str(raw_ref)))

        # Class propagation — intake may propose a different class than the
        # envelope was scoped as. brAIn's compute() uses that gap for the
        # class_agreement component; we keep both fields consistent by
        # placing the intake-proposed class in payload and mirroring
        # OutputContract.class_key to it, so gAIte can route accordingly.
        proposed_class = args.get("class_proposed") or envelope.class_key
        return OutputContract(
            envelope_id=envelope.envelope_id,
            class_key=proposed_class,
            decision="propose",
            payload=args,
            citations=citations,
            notes="intake ok",
            tokens_used=_token_count(response),
            wall_ms=wall_ms,
            provider_meta={
                "mocked": False,
                # model_id is stored under provider_meta so brAIn keeps it
                # out of any rendered payload projection. Server logs and
                # audit reads may still access it here.
                "model_id": self._model,
                "provider": INTAKE_PROVIDER,
            },
        )


def _token_count(response: Any) -> TokenCount:
    """Best-effort token count from the emergentintegrations response.

    Falls back to zeros if the response object doesn't expose usage yet;
    the AgentOutputProduced event still fires with what we have.
    """
    usage = getattr(response, "usage", None) or {}
    if hasattr(usage, "input_tokens"):
        return TokenCount(
            input=int(getattr(usage, "input_tokens", 0) or 0),
            output=int(getattr(usage, "output_tokens", 0) or 0),
        )
    if isinstance(usage, dict):
        return TokenCount(
            input=int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
            output=int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
        )
    raw = getattr(response, "raw", None)
    if raw is not None:
        # LiteLLM-shaped raw response
        u = getattr(raw, "usage", None)
        if u is not None:
            return TokenCount(
                input=int(getattr(u, "prompt_tokens", 0) or 0),
                output=int(getattr(u, "completion_tokens", 0) or 0),
            )
    return TokenCount(input=0, output=0)


def _empty_output(
    envelope: ContextEnvelope, wall_ms: int, response: Any, model_id: str,
    *, reason: str,
) -> OutputContract:
    """Shaped empty output — gAIte will assign schema_validity=0."""
    return OutputContract(
        envelope_id=envelope.envelope_id,
        class_key="_invalid_class_",
        decision="propose",
        payload={},
        citations=[],
        notes=f"intake refused: {reason}",
        tokens_used=_token_count(response),
        wall_ms=wall_ms,
        provider_meta={"mocked": False, "model_id": model_id, "refusal": reason},
    )
