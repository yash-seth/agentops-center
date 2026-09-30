"""OpenTelemetry metrics exposed to Prometheus.

Metric names (Prometheus form) are the contract with Grafana and the alert rules in deploy/:
  aoc_runs_total{status}                 aoc_run_duration_seconds (histogram)
  aoc_tool_calls_total{tool,status}      aoc_tool_duration_seconds (histogram)
  aoc_llm_tokens_total{model,direction}  aoc_run_cost_usd (histogram)
  aoc_cost_usd_total                     aoc_loops_detected_total{reason}
  aoc_guardrail_blocks_total{type}

Every metric carries agent, agent_version, tenant and env labels. run_id is deliberately NOT a
label (unbounded cardinality); it lives in traces and the run store instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from opentelemetry.metrics import Counter, Histogram
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader

from .telemetry import RunContext, current_run

DURATION_BUCKETS = [0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60]
COST_BUCKETS = [0.00001, 0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1]

METRIC_NAMES = (
    "aoc_runs_total",
    "aoc_run_duration_seconds",
    "aoc_tool_calls_total",
    "aoc_tool_duration_seconds",
    "aoc_llm_tokens_total",
    "aoc_run_cost_usd",
    "aoc_cost_usd_total",
    "aoc_loops_detected_total",
    "aoc_guardrail_blocks_total",
)


@dataclass
class _Instruments:
    provider: MeterProvider
    runs: Counter
    run_duration: Histogram
    tool_calls: Counter
    tool_duration: Histogram
    tokens: Counter
    run_cost: Histogram
    cost_total: Counter
    loops: Counter
    guardrail_blocks: Counter


_inst: _Instruments | None = None


def init_metrics(reader: MetricReader | None = None) -> MeterProvider:
    """Create the meter provider once. Default reader serves the Prometheus default registry."""
    global _inst
    if _inst is not None:
        return _inst.provider
    if reader is None:
        from opentelemetry.exporter.prometheus import PrometheusMetricReader

        reader = PrometheusMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    m = provider.get_meter("aoc_runtime")
    _inst = _Instruments(
        provider=provider,
        runs=m.create_counter("aoc_runs", description="Agent runs by final status"),
        run_duration=m.create_histogram(
            "aoc_run_duration", unit="s", description="End-to-end run latency",
            explicit_bucket_boundaries_advisory=DURATION_BUCKETS,
        ),
        tool_calls=m.create_counter("aoc_tool_calls", description="Tool calls by outcome"),
        tool_duration=m.create_histogram(
            "aoc_tool_duration", unit="s", description="Tool call latency",
            explicit_bucket_boundaries_advisory=DURATION_BUCKETS,
        ),
        tokens=m.create_counter("aoc_llm_tokens", description="LLM tokens used"),
        run_cost=m.create_histogram(
            "aoc_run_cost_usd", description="List-price cost per run in USD",
            explicit_bucket_boundaries_advisory=COST_BUCKETS,
        ),
        cost_total=m.create_counter("aoc_cost_usd", description="Cumulative list-price cost, USD"),
        loops=m.create_counter("aoc_loops_detected", description="Loops caught by the loop guard"),
        guardrail_blocks=m.create_counter("aoc_guardrail_blocks", description="Guardrail blocks"),
    )
    return provider


def reset_metrics_for_tests() -> None:
    global _inst
    _inst = None


def _labels(ctx: RunContext | None = None) -> dict[str, str]:
    ctx = ctx or current_run()
    if ctx is None:
        return {}
    return {
        "agent": ctx.agent_name,
        "agent_version": ctx.agent_version,
        "tenant": ctx.tenant_id,
        "env": ctx.environment,
    }


def record_tool_call(tool: str, status: str, duration_s: float) -> None:
    if _inst is None:
        return
    _inst.tool_calls.add(1, {**_labels(), "tool": tool, "status": status})
    _inst.tool_duration.record(duration_s, {**_labels(), "tool": tool})


def record_loop(reason: str) -> None:
    if _inst is not None:
        _inst.loops.add(1, {**_labels(), "reason": reason})


def record_guardrail_block(kind: str, *, agent: str = "", tenant: str = "", env: str = "") -> None:
    """Explicit labels are for blocks that happen before a run (and its context) exists."""
    if _inst is None:
        return
    labels = _labels() or {
        "agent": agent, "agent_version": "", "tenant": tenant, "env": env,
    }
    _inst.guardrail_blocks.add(1, {**labels, "type": kind})


def record_run(
    ctx: RunContext,
    *,
    status: str,
    duration_s: float,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> None:
    if _inst is None:
        return
    lb = _labels(ctx)
    _inst.runs.add(1, {**lb, "status": status})
    _inst.run_duration.record(duration_s, lb)
    _inst.run_cost.record(cost_usd, lb)
    _inst.cost_total.add(cost_usd, lb)
    _inst.tokens.add(input_tokens, {**lb, "model": model, "direction": "input"})
    _inst.tokens.add(output_tokens, {**lb, "model": model, "direction": "output"})
