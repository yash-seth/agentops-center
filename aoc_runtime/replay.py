"""Deterministic replay: feed a run's recorded model responses and tool results back through the
same graph, so the original trajectory is reproduced without calling any LLM or real tool.

Limits worth knowing: replays use what was *stored* (already redacted text), tool side effects are
not re-executed, and a replay cannot tell you what a different model or prompt would have done;
use "rerun" against another version for that.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, PrivateAttr

from .resilience import CircuitOpenError, ResilienceConfig

# Replays use their own breaker namespace and never retry: the recording already holds the outcome.
REPLAY_RESILIENCE = ResilienceConfig(
    max_retries=0, breaker_threshold=10**9, breaker_scope="replay:", timeout_s=30.0
)
TOOL_ERROR_PREFIX = "tool error: "


class ReplayDivergence(RuntimeError):
    """The graph asked for something the recording does not contain."""


class RecordedChatModel(BaseChatModel):
    """Returns the recorded model responses, in order."""

    responses: list[dict]
    _idx: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "recorded"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable:  # type: ignore[override]
        return self

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self._idx >= len(self.responses):
            raise ReplayDivergence("the recording has no further model responses")
        rec = self.responses[self._idx]
        self._idx += 1
        msg = AIMessage(
            content=rec["output"],
            tool_calls=rec["input"].get("tool_calls", []),
            usage_metadata={
                "input_tokens": rec["input_tokens"],
                "output_tokens": rec["output_tokens"],
                "total_tokens": rec["input_tokens"] + rec["output_tokens"],
            },
            response_metadata={"model_name": rec["name"]},
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])


class _AnyArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class RecordedTool(BaseTool):
    """Replays the recorded outcomes for one tool name, in order."""

    name: str
    description: str = "recorded tool (replay)"
    args_schema: type[BaseModel] = _AnyArgs
    outcomes: list[dict]
    _idx: int = PrivateAttr(default=0)

    def _run(self, *args: Any, **kwargs: Any) -> str:
        if self._idx >= len(self.outcomes):
            raise ReplayDivergence(f"the recording has no further results for tool {self.name!r}")
        out = self.outcomes[self._idx]
        self._idx += 1
        text = out["output"]
        if out["status"] == "ok":
            return text
        message = text.removeprefix(TOOL_ERROR_PREFIX)
        if out["status"] == "circuit_open":
            raise CircuitOpenError(message)
        raise RuntimeError(message)


def recorded_model(steps: list[dict]) -> RecordedChatModel:
    return RecordedChatModel(responses=[s for s in steps if s["kind"] == "llm"])


def recorded_tools(steps: list[dict]) -> list[RecordedTool]:
    by_tool: dict[str, list[dict]] = {}
    for s in steps:
        if s["kind"] == "tool":
            by_tool.setdefault(s["name"], []).append(s)
    return [RecordedTool(name=name, outcomes=outcomes) for name, outcomes in by_tool.items()]


def step_signature(step: dict) -> dict:
    """Stable fingerprint of a step, ignoring timing."""

    def h(value: Any) -> str:
        blob = json.dumps(value, sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()[:12]

    return {
        "kind": step["kind"],
        "name": step["name"],
        "status": step["status"],
        "input": h(step["input"]),
        "output": h(step["output"]),
    }
