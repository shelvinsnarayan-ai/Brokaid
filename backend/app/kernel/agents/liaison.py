"""liAIson — client-facing plain-English channel.

Fourth live-LLM agent. Answers client questions in Harbour voice,
always attributing to the broker by name and always ending with a
next step. Output flows through the phase-10 release pipeline
(deterministic coverage guard + complAInce classifier).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from emergentintegrations.llm.chat import LlmChat, UserMessage

from app.kernel.contracts.models import (
    ContextEnvelope,
    OutputContract,
    TokenCount,
)


LIAISON_MODEL = "claude-sonnet-4-6"
LIAISON_PROVIDER = "anthropic"


LIAISON_ELIGIBLE_CLASSES = frozenset({
    "comms_service",
    "request_info",
    "underwriter_qa",
    "renewal_pack_unchanged",
})


class AgentClassMismatch(Exception):
    """liAIson was asked to run on a class it does not handle."""


LIAISON_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "draft_client_reply",
        "description": (
            "Draft the plain-English reply to the client. This is the "
            "ONLY output surface — no prose outside the tool call."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "response_draft": {"type": "string"},
                "broker_attribution": {"type": "string"},
                "next_step": {"type": "string"},
                "citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "event_ref": {"type": "string"},
                            "field_path": {"type": "string"},
                        },
                        "required": ["event_ref"],
                    },
                },
                "detected_coverage_question": {"type": "boolean"},
            },
            "required": [
                "response_draft", "broker_attribution", "next_step",
                "citations", "detected_coverage_question",
            ],
        },
    },
}


def _load_system_prompt() -> str:
    return (
        Path(__file__).parent / "prompts" / "liaison.md"
    ).read_text(encoding="utf-8")


SYSTEM_PROMPT = _load_system_prompt()


def _user_message_text(*, client_message: str, context: dict[str, Any]) -> str:
    return (
        "CLIENT MESSAGE:\n"
        f"{client_message.strip()}\n\n"
        "CLIENT DISPLAY NAME: "
        f"{context.get('client_display_name') or '(unknown client)'}\n"
        "LINKED BROKER NAME: "
        f"{context.get('linked_broker_name') or '(unknown broker)'}\n"
        "MATTER KIND: "
        f"{context.get('matter_kind') or 'general'}\n"
        "RECENT EVENTS SUMMARY:\n"
        f"{context.get('recent_events_summary') or '(none)'}\n\n"
        "POLICY SUMMARY:\n"
        f"{context.get('policy_summary') or '(none)'}\n\n"
        "Return ONE call to the draft_client_reply tool. No prose."
    )


class EmergentLLMProviderLiaison:
    agent_name = "liaison"

    def __init__(self, *, api_key: str | None = None, model: str = LIAISON_MODEL):
        self._api_key = api_key or os.environ.get("EMERGENT_LLM_KEY")
        self._model = model

    async def run(self, envelope: ContextEnvelope) -> OutputContract:
        if envelope.class_key not in LIAISON_ELIGIBLE_CLASSES:
            raise AgentClassMismatch(
                f"liaison does not handle class {envelope.class_key!r}"
            )
        inputs = envelope.inputs or {}
        payload = await self.draft(
            client_message=inputs.get("client_message") or "",
            context=inputs.get("context") or {},
        )
        tokens = payload.pop("_tokens_used", {"input": 0, "output": 0})
        wall_ms = payload.pop("_wall_ms", 0)
        return OutputContract(
            envelope_id=envelope.envelope_id,
            class_key=envelope.class_key,
            decision="propose",
            payload=payload,
            citations=[],
            notes="liaison draft",
            tokens_used=TokenCount(
                input=int(tokens.get("input", 0)),
                output=int(tokens.get("output", 0)),
            ),
            wall_ms=int(wall_ms),
            provider_meta={
                "mocked": False,
                "model_id": self._model,
                "provider": LIAISON_PROVIDER,
            },
        )

    async def draft(
        self, *, client_message: str, context: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._api_key:
            raise RuntimeError(
                "EMERGENT_LLM_KEY missing; liaison requires a live provider."
            )
        chat = (
            LlmChat(
                api_key=self._api_key,
                session_id=f"liaison-{time.time_ns()}",
                system_message=SYSTEM_PROMPT,
            )
            .with_model(LIAISON_PROVIDER, self._model)
            .with_tools([LIAISON_TOOL], tool_choice="required")
        )
        user_text = _user_message_text(
            client_message=client_message, context=context,
        )
        start = time.monotonic()
        response = await chat.send_message_with_tools(UserMessage(text=user_text))
        wall_ms = int((time.monotonic() - start) * 1000)

        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            return _safe_hitl_draft(
                broker=context.get("linked_broker_name") or "your broker",
                tokens=_tokens(response), wall_ms=wall_ms,
            )
        tc = tool_calls[0]
        try:
            args = (
                tc.arguments if isinstance(tc.arguments, dict)
                else json.loads(tc.arguments)
            )
        except (TypeError, ValueError):
            return _safe_hitl_draft(
                broker=context.get("linked_broker_name") or "your broker",
                tokens=_tokens(response), wall_ms=wall_ms,
            )

        # Belt-and-braces: enforce non-empty attribution + next_step.
        broker_name = (
            context.get("linked_broker_name") or "your broker"
        )
        if not (args.get("broker_attribution") or "").strip():
            args["broker_attribution"] = (
                f"Your broker, {broker_name}, is across this."
            )
        if not (args.get("next_step") or "").strip():
            args["next_step"] = (
                "We'll be in touch shortly."
            )
        if "detected_coverage_question" not in args:
            args["detected_coverage_question"] = False
        args.setdefault("citations", [])

        args["_tokens_used"] = _tokens(response)
        args["_wall_ms"] = wall_ms
        return args


def _tokens(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None) or {}
    if hasattr(usage, "input_tokens"):
        return {
            "input": int(getattr(usage, "input_tokens", 0) or 0),
            "output": int(getattr(usage, "output_tokens", 0) or 0),
        }
    if isinstance(usage, dict):
        return {
            "input": int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
            "output": int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
        }
    return {"input": 0, "output": 0}


def _safe_hitl_draft(
    *, broker: str, tokens: dict[str, int], wall_ms: int,
) -> dict[str, Any]:
    return {
        "response_draft": (
            "I'll get your broker to reply on this one directly."
        ),
        "broker_attribution": f"Your broker, {broker}, has this queued.",
        "next_step": "Watch this thread — a written reply is on its way.",
        "citations": [],
        "detected_coverage_question": True,
        "_tokens_used": tokens,
        "_wall_ms": wall_ms,
    }
