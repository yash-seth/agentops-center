"""Reproducible reliability benchmarks. Everything is seeded, offline, and takes a few seconds.

  uv run --python 3.12 python scripts/benchmark_resilience.py            # markdown tables
  uv run --python 3.12 python scripts/benchmark_resilience.py --json     # machine readable

1. Retries: tool-call success rate at a given injected failure rate, with and without retries.
2. Circuit breaker: calls that still reach a dependency during a total outage, with and without it.
3. Loop guard: share of forced loops that are stopped, and how many tool executions they cost.
"""

from __future__ import annotations

import argparse
import json
import os

os.environ.setdefault("AOC_EMBEDDER", "hash")
os.environ.setdefault("AOC_TELEMETRY_ENABLED", "false")

from langchain_core.tools import tool  # noqa: E402

from aoc_runtime import faults, resilience  # noqa: E402
from aoc_runtime.faults import Fault  # noqa: E402
from aoc_runtime.graph_base import _execute_tool  # noqa: E402
from aoc_runtime.llm import FakeChatModel  # noqa: E402
from aoc_runtime.resilience import ResilienceConfig  # noqa: E402
from aoc_runtime.runner import run_agent  # noqa: E402

CALLS = 2000
ERROR_RATES = (0.0, 0.25, 0.5, 0.75)


@tool
def bench_tool(query: str) -> str:
    """A trivial dependency used only for benchmarking."""
    return "ok"


def _call(cfg: ResilienceConfig) -> tuple[str, int]:
    _, status, attempts, _ = _execute_tool(
        {"name": "bench_tool", "args": {"query": "q"}, "id": "c"}, bench_tool, cfg
    )
    return status, attempts


def retry_benchmark(calls: int = CALLS, rates=ERROR_RATES, seed: int = 42) -> list[dict]:
    rows = []
    for rate in rates:
        row: dict = {"injected_failure_rate": rate}
        for label, retries in (("no_retries", 0), ("two_retries", 2)):
            resilience.reset_breakers()
            faults.set_faults([Fault("bench_tool", error_rate=rate)], seed=seed)
            cfg = ResilienceConfig(
                max_retries=retries, backoff_s=0, breaker_threshold=10**9, timeout_s=5
            )
            ok = sum(1 for _ in range(calls) if _call(cfg)[0] == "ok")
            row[f"success_{label}"] = ok / calls
        row["theoretical_two_retries"] = 1 - rate**3
        rows.append(row)
    faults.clear_faults()
    resilience.reset_breakers()
    return rows


def breaker_benchmark(calls: int = 200, seed: int = 42) -> dict:
    """During a total outage, how many calls still hit the dependency?"""
    result: dict = {"calls": calls}
    for label, threshold in (("without_breaker", 10**9), ("with_breaker", 5)):
        resilience.reset_breakers()
        faults.set_faults([Fault("bench_tool", error_rate=1.0)], seed=seed)
        cfg = ResilienceConfig(
            max_retries=2, backoff_s=0, breaker_threshold=threshold, breaker_cooldown_s=3600
        )
        reached = 0
        for _ in range(calls):
            status, attempts = _call(cfg)
            # A call the breaker finally refused made `attempts - 1` real dependency calls;
            # the refused attempt itself never left the process.
            reached += attempts - (1 if status == "circuit_open" else 0)
        result[f"dependency_calls_{label}"] = reached
    result["dependency_calls_avoided"] = 1 - (
        result["dependency_calls_with_breaker"] / result["dependency_calls_without_breaker"]
    )
    faults.clear_faults()
    resilience.reset_breakers()
    return result


def loop_benchmark() -> list[dict]:
    rows = []
    cases = (
        ("hr-policy-bot", "rag_search", "How many sick days?"),
        ("supply-chain-assistant", "rag_search", "What is the lead time for imports?"),
        ("supply-chain-assistant", "inventory_sql", "Stock of SKU-1001?"),
    )
    for agent, tool_name, question in cases:
        faults.set_faults([Fault(tool_name, force_loop=True)])
        resilience.reset_breakers()
        res = run_agent(agent, question, llm=FakeChatModel())
        rows.append(
            {
                "agent": agent, "looping_tool": tool_name, "stopped": res.status == "loop_stopped",
                "reason": res.loop_reason,
                "tool_executions": sum(1 for s in res.step_records if s.kind == "tool"),
            }
        )
    faults.clear_faults()
    return rows


def run_all() -> dict:
    return {
        "retries": retry_benchmark(),
        "breaker": breaker_benchmark(),
        "loops": loop_benchmark(),
    }


def to_markdown(results: dict) -> str:
    out = [f"### Retries ({CALLS} tool calls per cell)", "",
           "| injected failure rate | success, no retries | success, 2 retries | theory |",
           "|---|---|---|---|"]
    for r in results["retries"]:
        out.append(
            f"| {r['injected_failure_rate']:.0%} | {r['success_no_retries']:.1%} | "
            f"{r['success_two_retries']:.1%} | {r['theoretical_two_retries']:.1%} |"
        )
    b = results["breaker"]
    out += ["", f"### Circuit breaker (total outage, {b['calls']} agent tool calls)", "",
            "| | calls that reached the failing dependency |", "|---|---|",
            f"| without breaker | {b['dependency_calls_without_breaker']} |",
            f"| with breaker | {b['dependency_calls_with_breaker']} |",
            f"| avoided | {b['dependency_calls_avoided']:.1%} |", "",
            "### Loop guard (forced loops)", "",
            "| agent | looping tool | stopped | reason | tool executions before stop |",
            "|---|---|---|---|---|"]
    for r in results["loops"]:
        out.append(
            f"| {r['agent']} | {r['looping_tool']} | {'yes' if r['stopped'] else 'NO'} | "
            f"{r['reason']} | {r['tool_executions']} |"
        )
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    results = run_all()
    print(json.dumps(results, indent=2) if args.json else to_markdown(results))


if __name__ == "__main__":
    main()
