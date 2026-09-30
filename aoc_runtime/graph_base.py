"""LangGraph agent loop: model -> (tool calls -> tools -> model)* -> answer.

Tool execution is done here (not by a prebuilt node) so every call gets a span with
``aoc.tool.*`` attributes, fault injection, metrics and loop detection. Tool errors become
observable ToolMessages instead of crashes.
"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from . import faults, metrics
from . import semconv as sc
from .cost import cost_usd
from .loop_guard import FALLBACK_ANSWER, LoopGuard
from .telemetry import get_tracer


class AgentState(MessagesState):
    loop_reason: str | None


def _run_cost(messages: list) -> float:
    total = 0.0
    for m in messages:
        if isinstance(m, AIMessage) and m.usage_metadata:
            model = (m.response_metadata or {}).get("model_name", "")
            total += cost_usd(
                model, m.usage_metadata["input_tokens"], m.usage_metadata["output_tokens"]
            )
    return total


def _flag_loop(reason: str) -> None:
    span = trace.get_current_span()
    span.set_attribute(sc.LOOP_DETECTED, True)
    span.add_event("loop_detected", {"reason": reason})
    metrics.record_loop(reason)


def build_graph(
    llm: BaseChatModel,
    tools: list[Any],
    system_prompt: str,
    *,
    max_steps: int = 6,
    cost_budget_usd: float = 0.05,
):
    by_name = {t.name: t for t in tools}
    bound = llm.bind_tools(tools)
    guard = LoopGuard(max_steps=max_steps, cost_budget_usd=cost_budget_usd)

    def call_model(state: AgentState) -> dict:
        reason = guard.observe_step() or guard.observe_cost(_run_cost(state["messages"]))
        if reason is not None:
            _flag_loop(reason)
            return {"messages": [AIMessage(FALLBACK_ANSWER)], "loop_reason": reason}
        looping_tool = faults.forced_loop_tool()
        if looping_tool in by_name:
            question = next(m.content for m in state["messages"] if isinstance(m, HumanMessage))
            args = {"sku": "SKU-1001"} if looping_tool == "inventory_sql" else {"query": question}
            call = {"name": looping_tool, "args": args, "id": f"call_{guard.steps}"}
            return {"messages": [AIMessage("", tool_calls=[call])]}
        msgs = [SystemMessage(system_prompt), *state["messages"]]
        return {"messages": [bound.invoke(msgs)]}

    def run_tools(state: AgentState) -> dict:
        last: AIMessage = state["messages"][-1]  # type: ignore[assignment]
        out: list[ToolMessage | AIMessage] = []
        for call in last.tool_calls:
            if (reason := guard.observe_call(call["name"], call["args"])) is not None:
                _flag_loop(reason)
                out.append(AIMessage(FALLBACK_ANSWER))
                return {"messages": out, "loop_reason": reason}
            with get_tracer().start_as_current_span(f"tool.{call['name']}") as span:
                span.set_attribute(sc.TOOL_NAME, call["name"])
                t0 = time.perf_counter()
                status = "ok"
                try:
                    faults.apply_tool_faults(call["name"])
                    result = by_name[call["name"]].invoke(call["args"])
                except Exception as exc:  # noqa: BLE001 - tool failure is data, not a crash
                    status, result = "error", f"tool error: {exc}"
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                elapsed = time.perf_counter() - t0
                span.set_attribute(sc.TOOL_STATUS, status)
                span.set_attribute("aoc.tool.latency_ms", elapsed * 1000)
            metrics.record_tool_call(call["name"], status, elapsed)
            out.append(ToolMessage(content=str(result), tool_call_id=call["id"], name=call["name"]))
        return {"messages": out}

    def after_model(state: AgentState) -> str:
        if state.get("loop_reason"):
            return END
        return "tools" if getattr(state["messages"][-1], "tool_calls", None) else END

    def after_tools(state: AgentState) -> str:
        return END if state.get("loop_reason") else "model"

    g = StateGraph(AgentState)
    g.add_node("model", call_model)
    g.add_node("tools", run_tools)
    g.add_edge(START, "model")
    g.add_conditional_edges("model", after_model, {"tools": "tools", END: END})
    g.add_conditional_edges("tools", after_tools, {"model": "model", END: END})
    return g.compile()
