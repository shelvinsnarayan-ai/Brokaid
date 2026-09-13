"""plAIce — the placement agent.

Second live-LLM agent. Drafts a submission from intAIke's parsed
proposal and hands off to the MOCKED CarrierChannel for a quote.

Pinned to the same model as intake for stack-simplicity; the deny-list
scan is a shared set, so a change here needs a matching CI update.
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


PLACEMENT_MODEL = "claude-sonnet-4-6"
PLACEMENT_PROVIDER = "anthropic"

PLACEMENT_ELIGIBLE_CLASSES = frozenset({
    "submit_to_market",
    "endorsement_execute_instructed_quoted",
    "endorsement_standard",
    "renewal_pack_unchanged",
})


class AgentClassMismatch(Exception):
    """plAIce was asked to run on a class it does not handle."""


_ADVICE_REROUTE = ["comms_advisory", "bind_new_or_remarket", None]

PLACEMENT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "record_placement_draft",
        "description": "Record the drafted submission for market. This is the ONLY output surface.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "submission_summary": {"type": "string"},
                "structured_submission": {
                    "type": "object",
                    "additionalProperties": True,
                    "properties": {
                        "risk_description": {"type": "string"},
                        "cover_type": {"type": "string"},
                        "requested_changes": {"type": "array"},
                        "underwriter_questions_answered": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": True,
                                "properties": {
                                    "question": {"type": "string"},
                                    "answer": {"type": "string"},
                                    "citation": {"type": ["object", "null"]},
                                },
                                "required": ["question", "answer"],
                            },
                        },
                        "market_targets": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
                "recommended_wording_ref": {"type": ["string", "null"]},
                "notes": {"type": "string"},
                "class_reroute": {
                    "type": ["string", "null"],
                    "enum": _ADVICE_REROUTE,
                },
            },
            "required": [
                "submission_summary", "structured_submission",
                "notes", "class_reroute",
            ],
        },
    },
}


def _load_system_prompt() -> str:
    return (Path(__file__).parent / "prompts" / "placement.md").read_text(encoding="utf-8")


SYSTEM_PROMPT = _load_system_prompt()


def _user_message_text(envelope: ContextEnvelope) -> str:
    inputs = envelope.inputs or {}
    return (
        "INTAKE OUTPUT (from the previous step):\n"
        f"{json.dumps(inputs.get('intake_output') or {}, indent=2)}\n\n"
        "POLICY CURRENT SCHEDULE:\n"
        f"{json.dumps(inputs.get('policy_current') or {}, indent=2)}\n\n"
        "CONTEXT SUMMARY:\n"
        f"{inputs.get('context_events_summary') or '(none)'}\n\n"
        "Return ONE call to the record_placement_draft tool. No prose."
    )


class EmergentLLMProviderPlacement:
    agent_name = "placement"

    def __init__(self, *, api_key: str | None = None, model: str = PLACEMENT_MODEL):
        self._api_key = api_key or os.environ.get("EMERGENT_LLM_KEY")
        self._model = model

    async def run(self, envelope: ContextEnvelope) -> OutputContract:
        if envelope.class_key not in PLACEMENT_ELIGIBLE_CLASSES:
            raise AgentClassMismatch(
                f"placement does not handle class {envelope.class_key!r}"
            )
        if not self._api_key:
            raise RuntimeError("EMERGENT_LLM_KEY missing; placement requires a live provider.")

        chat = (
            LlmChat(
                api_key=self._api_key,
                session_id=f"placement-{envelope.envelope_id}",
                system_message=SYSTEM_PROMPT,
            )
            .with_model(PLACEMENT_PROVIDER, self._model)
            .with_tools([PLACEMENT_TOOL], tool_choice="required")
        )

        start = time.monotonic()
        response = await chat.send_message_with_tools(
            UserMessage(text=_user_message_text(envelope))
        )
        wall_ms = int((time.monotonic() - start) * 1000)

        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            return _empty(envelope, wall_ms, response, self._model, reason="no_tool_call")

        tc = tool_calls[0]
        try:
            args = tc.arguments if isinstance(tc.arguments, dict) else json.loads(tc.arguments)
        except (TypeError, ValueError) as e:
            return _empty(envelope, wall_ms, response, self._model, reason=f"bad_json:{e!s}")

        citations: list[Citation] = []
        cover = ((args.get("structured_submission") or {}).get("cover_type"))
        if cover:
            citations.append(Citation(kind="risk_schema", ref=f"cover_type:{cover}"))
        # cite every question-answer citation as an event ref.
        for q in (args.get("structured_submission") or {}).get("underwriter_questions_answered") or []:
            c = q.get("citation")
            if isinstance(c, dict) and c.get("event_id"):
                citations.append(Citation(kind="event", ref=str(c["event_id"])))

        # If plAIce reroutes, class_key on the OutputContract mirrors the reroute.
        reroute = args.get("class_reroute")
        proposed_class = reroute or envelope.class_key
        return OutputContract(
            envelope_id=envelope.envelope_id,
            class_key=proposed_class,
            decision="propose",
            payload=args,
            citations=citations,
            notes="placement ok",
            tokens_used=_tokens(response),
            wall_ms=wall_ms,
            provider_meta={
                "mocked": False,
                "model_id": self._model,
                "provider": PLACEMENT_PROVIDER,
            },
        )


def _tokens(response: Any) -> TokenCount:
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
    return TokenCount(input=0, output=0)


def _empty(envelope: ContextEnvelope, wall_ms: int, response: Any,
           model_id: str, *, reason: str) -> OutputContract:
    return OutputContract(
        envelope_id=envelope.envelope_id,
        class_key="_invalid_class_",
        decision="propose",
        payload={},
        citations=[],
        notes=f"placement refused: {reason}",
        tokens_used=_tokens(response),
        wall_ms=wall_ms,
        provider_meta={"mocked": False, "model_id": model_id, "refusal": reason},
    )
