"""complAInce — the compliance / advice-classification agent.

Third live-LLM agent. Runs after the deterministic coverage-language
guard (see `app.kernel.gate.coverage_guard`) passes and classifies the
draft as factual | opinion | recommendation while flagging risks.

Pinned to the same model as intake / placement for stack simplicity;
the deny-list scan is a shared set, so a change here needs a matching
CI update.
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


# ---- Pinned model (never on screen; deny-list scans the built bundle)
COMPLIANCE_MODEL = "claude-sonnet-4-6"
COMPLIANCE_PROVIDER = "anthropic"


# ---- Class allow-list ------------------------------------------------------
# These are the only gAIte classes for which the compliance agent will
# accept an envelope. Every other class → AgentClassMismatch → brAIn
# emits AgentInvocationCompleted{outcome:'error'}.
COMPLIANCE_ELIGIBLE_CLASSES = frozenset({
    "comms_service",
    "comms_advisory",
    "renewal_pack_unchanged",
    "claim_admin",
    "issue_documents",
})


class AgentClassMismatch(Exception):
    """complAInce was asked to run on a class it does not handle."""


# ---- Tool schema -----------------------------------------------------------
_CLASSIFICATIONS = ["factual", "opinion", "recommendation"]
_RISK_KINDS = [
    "coverage_position",
    "premium_speculation",
    "regulatory_notice_missing",
    "vulnerable_flag_absent",
    "personal_statement",
]
_SEVERITIES = ["info", "warn", "block"]
_VERDICTS = ["release", "rewrite", "hitl"]

COMPLIANCE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "classify_client_message",
        "description": (
            "Classify the proposed client-facing message. This is the ONLY "
            "output surface — do not write prose."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "advice_classification": {
                    "type": "string", "enum": _CLASSIFICATIONS,
                },
                "classification_rationale": {"type": "string"},
                "risks_flagged": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "kind": {"type": "string", "enum": _RISK_KINDS},
                            "span": {"type": "string"},
                            "severity": {"type": "string", "enum": _SEVERITIES},
                        },
                        "required": ["kind", "span", "severity"],
                    },
                },
                "recommended_rewrite": {"type": ["string", "null"]},
                "release_verdict": {"type": "string", "enum": _VERDICTS},
            },
            "required": [
                "advice_classification", "classification_rationale",
                "risks_flagged", "recommended_rewrite", "release_verdict",
            ],
        },
    },
}


def _load_system_prompt() -> str:
    return (
        Path(__file__).parent / "prompts" / "compliance.md"
    ).read_text(encoding="utf-8")


SYSTEM_PROMPT = _load_system_prompt()


def _user_message_text(
    *, draft: str, context: dict[str, Any], author_agent: str,
) -> str:
    return (
        "DRAFT CLIENT-FACING MESSAGE:\n"
        f"{draft.strip()}\n\n"
        "MATTER KIND: "
        f"{context.get('matter_kind') or 'general'}\n"
        "CLIENT CONTEXT SUMMARY:\n"
        f"{context.get('client_context_summary') or '(none)'}\n\n"
        "RECENT EVENTS SUMMARY:\n"
        f"{context.get('recent_events_summary') or '(none)'}\n\n"
        f"DRAFT AUTHOR AGENT: {author_agent!r}\n\n"
        "Return ONE call to the classify_client_message tool. No prose."
    )


class EmergentLLMProviderCompliance:
    """Live compliance classifier.

    Two entry points:
      * `run(envelope)`  — implements the AgentProvider Protocol so
        brAIn can invoke us with the full ledger-event lifecycle
        (AgentInvocationStarted / AgentOutputProduced /
        AgentInvocationCompleted). `envelope.inputs` must carry
        `draft` + `context` + `author_agent`.
      * `classify(...)`  — bare classifier for `try_release()` in
        `app.kernel.release`; returns the parsed JSON verdict plus
        budget metadata for the release event payload.
    """

    agent_name = "compliance"

    def __init__(
        self, *, api_key: str | None = None, model: str = COMPLIANCE_MODEL,
    ):
        self._api_key = api_key or os.environ.get("EMERGENT_LLM_KEY")
        self._model = model

    # ---- brAIn-compatible provider entry ----------------------------------
    async def run(self, envelope: ContextEnvelope) -> OutputContract:
        if envelope.class_key not in COMPLIANCE_ELIGIBLE_CLASSES:
            raise AgentClassMismatch(
                f"compliance does not handle class {envelope.class_key!r}"
            )
        inputs = envelope.inputs or {}
        draft = inputs.get("message_draft") or inputs.get("draft") or ""
        context = inputs.get("context") or {}
        author = inputs.get("author_agent") or "unknown"
        verdict = await self.classify(
            draft=draft, context=context, author_agent=author,
        )
        tokens = verdict.pop("_tokens_used", {"input": 0, "output": 0})
        wall_ms = verdict.pop("_wall_ms", 0)
        return OutputContract(
            envelope_id=envelope.envelope_id,
            class_key=envelope.class_key,
            decision="propose",
            payload=verdict,
            citations=[],
            notes="compliance ok",
            tokens_used=TokenCount(
                input=int(tokens.get("input", 0)),
                output=int(tokens.get("output", 0)),
            ),
            wall_ms=int(wall_ms),
            provider_meta={
                "mocked": False,
                "model_id": self._model,
                "provider": COMPLIANCE_PROVIDER,
            },
        )

    # ---- Direct-call classifier used by `try_release()` -------------------
    async def classify(
        self, *, draft: str, context: dict[str, Any], author_agent: str,
    ) -> dict[str, Any]:
        """Return the classifier verdict + `_tokens_used` + `_wall_ms`.

        Never raises on model refusal; if the tool call is missing or
        malformed, returns a safe-HITL verdict so the release pipeline
        can escalate instead of releasing on ambiguity.
        """
        if not self._api_key:
            raise RuntimeError(
                "EMERGENT_LLM_KEY missing; compliance requires a live provider."
            )
        chat = (
            LlmChat(
                api_key=self._api_key,
                session_id=f"compliance-{time.time_ns()}",
                system_message=SYSTEM_PROMPT,
            )
            .with_model(COMPLIANCE_PROVIDER, self._model)
            .with_tools([COMPLIANCE_TOOL], tool_choice="required")
        )
        user_text = _user_message_text(
            draft=draft, context=context, author_agent=author_agent,
        )
        start = time.monotonic()
        response = await chat.send_message_with_tools(
            UserMessage(text=user_text)
        )
        wall_ms = int((time.monotonic() - start) * 1000)

        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            return _safe_hitl(
                reason="no_tool_call",
                tokens=_tokens(response),
                wall_ms=wall_ms,
            )
        tc = tool_calls[0]
        try:
            args = (
                tc.arguments if isinstance(tc.arguments, dict)
                else json.loads(tc.arguments)
            )
        except (TypeError, ValueError) as e:
            return _safe_hitl(
                reason=f"bad_json:{e!s}",
                tokens=_tokens(response),
                wall_ms=wall_ms,
            )

        # Enforce the "block risk ⇒ rewrite/hitl" invariant at parse time.
        risks = args.get("risks_flagged") or []
        any_block = any(
            (r.get("severity") == "block") for r in risks
        )
        if any_block and args.get("release_verdict") == "release":
            args["release_verdict"] = (
                "hitl"
                if any(r.get("kind") == "coverage_position" for r in risks)
                else "rewrite"
            )

        # If classification is recommendation and verdict is release, escalate.
        if (
            args.get("advice_classification") == "recommendation"
            and args.get("release_verdict") == "release"
        ):
            args["release_verdict"] = "hitl"

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
            "input": int(
                usage.get("input_tokens") or usage.get("prompt_tokens") or 0
            ),
            "output": int(
                usage.get("output_tokens") or usage.get("completion_tokens") or 0
            ),
        }
    return {"input": 0, "output": 0}


def _safe_hitl(*, reason: str, tokens: dict[str, int], wall_ms: int) -> dict[str, Any]:
    """Return a HITL verdict when the classifier response is unusable."""
    return {
        "advice_classification": "opinion",
        "classification_rationale": f"classifier unavailable ({reason})",
        "risks_flagged": [{
            "kind": "personal_statement",
            "span": "",
            "severity": "block",
        }],
        "recommended_rewrite": None,
        "release_verdict": "hitl",
        "_tokens_used": tokens,
        "_wall_ms": wall_ms,
    }
