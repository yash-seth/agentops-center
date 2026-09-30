"""Run a few agent questions and print results. Traces go to OTEL_EXPORTER_OTLP_ENDPOINT.

Usage: uv run --python 3.12 python scripts/demo_run.py
Set AOC_LLM_PROVIDERS=fake (default falls back to fake when no API keys are set) to run offline.
"""

from aoc_runtime.runner import run_agent
from aoc_runtime.telemetry import init_telemetry

QUESTIONS = [
    "What is the escalation rule when stock cover drops below 3 days?",
    "What is the current stock and demand for SKU-1001?",
    "What is the standard lead time for imported ingredients?",
]

if __name__ == "__main__":
    provider = init_telemetry("aoc-supply-chain-assistant")
    for q in QUESTIONS:
        r = run_agent("supply-chain-assistant", q)
        print(f"{r.run_id[:8]} {r.status} steps={r.steps} tools={r.tool_calls} "
              f"tokens={r.input_tokens}/{r.output_tokens} cost=${r.cost_usd:.6f}")
        print("  ", r.answer[:160].replace("\n", " "))
    provider.force_flush()
