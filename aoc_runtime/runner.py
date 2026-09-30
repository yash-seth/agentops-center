"""Runs one agent invocation inside a RunContext + root span. run_id == OTel trace_id."""

from __future__ import annotations

import importlib
import time
from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage
from opentelemetry.trace import Status, StatusCode

from . import semconv as sc
from .config import REPO_ROOT, get_settings
from .cost import cost_usd
from .graph_base import build_graph
from .llm import get_llm, provider_of
from .spec import AgentSpec, load_spec
from .telemetry import RunContext, get_tracer, run_scope


@dataclass
class RunResult:
    run_id: str
    agent: str
    version: str
    status: str
    answer: str
    steps: int
    tool_calls: list[str]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_s: float


def load_agent(name: str) -> tuple[AgentSpec, list]:
    agent_dir = REPO_ROOT / "agents" / name.replace("-", "_")
    spec = load_spec(agent_dir)
    module = importlib.import_module(f"agents.{agent_dir.name}.tools")
    return spec, module.TOOLS


def run_agent(name: str, question: str, *, llm=None, tenant: str | None = None) -> RunResult:
    settings = get_settings()
    spec, tools = load_agent(name)
    llm = llm or get_llm()
    tracer = get_tracer()
    t0 = time.perf_counter()

    with tracer.start_as_current_span("agent.run") as root:
        run_id = format(root.get_span_context().trace_id, "032x")
        ctx = RunContext(
            agent_name=spec.name,
            agent_version=spec.version,
            prompt_version=spec.prompt_version,
            tenant_id=tenant or spec.tenant,
            app_id=spec.app_id,
            run_id=run_id,
            environment=settings.aoc_env,
        )
        root.set_attributes(ctx.attributes())
        with run_scope(ctx):
            status, answer, messages = "ok", "", []
            try:
                graph = build_graph(llm, tools, spec.system_prompt)
                final = graph.invoke(
                    {"messages": [HumanMessage(question)]},
                    {"recursion_limit": spec.limits.max_steps * 2 + 1},
                )
                messages = final["messages"]
                answer = str(messages[-1].content)
            except Exception as exc:  # noqa: BLE001
                status, answer = "error", f"run failed: {exc}"
                root.record_exception(exc)
                root.set_status(Status(StatusCode.ERROR, str(exc)))

            ai = [m for m in messages if isinstance(m, AIMessage)]
            tin = sum((m.usage_metadata or {}).get("input_tokens", 0) for m in ai)
            tout = sum((m.usage_metadata or {}).get("output_tokens", 0) for m in ai)
            model = (
                next(
                    (m.response_metadata.get("model_name") for m in ai if m.response_metadata), None
                )
                or "fake-llm"
            )
            cost = cost_usd(model, tin, tout)
            tool_calls = [tc["name"] for m in ai for tc in (m.tool_calls or [])]
            root.set_attributes(
                {
                    sc.RUN_STATUS: status,
                    sc.RUN_STEPS: len(ai),
                    sc.GEN_AI_SYSTEM: provider_of(llm),
                    sc.GEN_AI_REQUEST_MODEL: model,
                    sc.GEN_AI_USAGE_INPUT_TOKENS: tin,
                    sc.GEN_AI_USAGE_OUTPUT_TOKENS: tout,
                    sc.COST_USD: cost,
                }
            )

    return RunResult(
        run_id, spec.name, spec.version, status, answer, len(ai), tool_calls, tin, tout, cost,
        time.perf_counter() - t0,
    )
