"""LangGraph agent loop: model -> (tool calls -> tools -> model)* -> answer.

Tool execution is done here (not by a prebuilt node) so every call gets a span with
``aoc.tool.*`` attributes and errors become observable ToolMessages instead of crashes.
"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from opentelemetry.trace import Status, StatusCode

from . import semconv as sc
from .telemetry import get_tracer


def build_graph(llm: BaseChatModel, tools: list[Any], system_prompt: str):
    by_name = {t.name: t for t in tools}
    bound = llm.bind_tools(tools)

    def call_model(state: MessagesState) -> dict:
        msgs = [SystemMessage(system_prompt), *state["messages"]]
        return {"messages": [bound.invoke(msgs)]}

    def run_tools(state: MessagesState) -> dict:
        last: AIMessage = state["messages"][-1]  # type: ignore[assignment]
        out: list[ToolMessage] = []
        for call in last.tool_calls:
            with get_tracer().start_as_current_span(f"tool.{call['name']}") as span:
                span.set_attribute(sc.TOOL_NAME, call["name"])
                t0 = time.perf_counter()
                try:
                    result = by_name[call["name"]].invoke(call["args"])
                    span.set_attribute(sc.TOOL_STATUS, "ok")
                except Exception as exc:  # noqa: BLE001 - tool failure is data, not a crash
                    result = f"tool error: {exc}"
                    span.set_attribute(sc.TOOL_STATUS, "error")
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.set_attribute("aoc.tool.latency_ms", (time.perf_counter() - t0) * 1000)
            out.append(ToolMessage(content=str(result), tool_call_id=call["id"], name=call["name"]))
        return {"messages": out}

    def route(state: MessagesState) -> str:
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else END

    g = StateGraph(MessagesState)
    g.add_node("model", call_model)
    g.add_node("tools", run_tools)
    g.add_edge(START, "model")
    g.add_conditional_edges("model", route, {"tools": "tools", END: END})
    g.add_edge("tools", "model")
    return g.compile()
