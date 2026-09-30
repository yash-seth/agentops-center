"""LLM provider router: gemini -> groq -> ollama -> fake, with fallback on failure.

``FakeChatModel`` is deterministic and free, so unit tests and CI never need an API key. It
follows a simple script: call ``rag_search`` first, then any other tool whose name appears in the
question hints, then answer using the tool outputs it has seen.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable

from .config import get_settings


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


class FakeChatModel(BaseChatModel):
    model_name: str = "fake-llm"
    tool_names: list[str] = []
    # tool name -> args builder key; overridable so tests can force loops
    force_tool: str | None = None

    @property
    def _llm_type(self) -> str:
        return "fake"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable:  # type: ignore[override]
        names = [getattr(t, "name", None) or t["name"] for t in tools]
        return self.model_copy(update={"tool_names": names})

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        question = next((m.content for m in messages if isinstance(m, HumanMessage)), "")
        question = question if isinstance(question, str) else json.dumps(question)
        called = [
            tc["name"] for m in messages if isinstance(m, AIMessage) for tc in (m.tool_calls or [])
        ]
        observations = [m.content for m in messages if isinstance(m, ToolMessage)]

        msg: AIMessage
        if self.force_tool and self.force_tool in self.tool_names:
            msg = self._tool_call(self.force_tool, question, len(called))
        elif "rag_search" in self.tool_names and "rag_search" not in called:
            msg = self._tool_call("rag_search", question, 0)
        elif "inventory_sql" in self.tool_names and "inventory_sql" not in called and any(
            w in question.lower() for w in ("stock", "inventory", "sku", "demand", "units")
        ):
            msg = self._tool_call("inventory_sql", question, 0)
        else:
            body = " | ".join(str(o)[:160] for o in observations) or "no context available"
            msg = AIMessage(content=f"Based on the retrieved context: {body}")

        prompt_tokens = sum(_tokens(str(m.content)) for m in messages)
        out_tokens = _tokens(str(msg.content) + json.dumps(msg.tool_calls))
        msg.usage_metadata = {
            "input_tokens": prompt_tokens,
            "output_tokens": out_tokens,
            "total_tokens": prompt_tokens + out_tokens,
        }
        msg.response_metadata = {"model_name": self.model_name}
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @staticmethod
    def _tool_call(name: str, question: str, n: int) -> AIMessage:
        args = {"sku": "SKU-1001"} if name == "inventory_sql" else {"query": question}
        return AIMessage(
            content="", tool_calls=[{"name": name, "args": args, "id": f"call_{name}_{n}"}]
        )


def _build(provider: str) -> BaseChatModel:
    s = get_settings()
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=s.aoc_gemini_model, google_api_key=s.google_api_key, temperature=0
        )
    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(model=s.aoc_groq_model, api_key=s.groq_api_key, temperature=0)
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(model=s.aoc_ollama_model, temperature=0)
    if provider == "fake":
        return FakeChatModel()
    raise ValueError(f"unknown provider {provider!r}")


def _has_credentials(provider: str) -> bool:
    s = get_settings()
    return {
        "gemini": bool(s.google_api_key),
        "groq": bool(s.groq_api_key),
        "ollama": True,
        "fake": True,
    }.get(provider, False)


def get_llm(providers: list[str] | None = None) -> BaseChatModel:
    """Primary provider with the remaining ones as runtime fallbacks."""
    chain = [p for p in (providers or get_settings().providers) if _has_credentials(p)]
    if not chain:
        chain = ["fake"]
    models = [_build(p) for p in chain]
    primary, fallbacks = models[0], models[1:]
    return primary.with_fallbacks(fallbacks) if fallbacks else primary  # type: ignore[return-value]


def provider_of(model: BaseChatModel) -> str:
    name = type(model).__name__.lower()
    for key, label in (("google", "gemini"), ("groq", "groq"), ("ollama", "ollama")):
        if key in name:
            return label
    return "fake" if "fake" in name else name
