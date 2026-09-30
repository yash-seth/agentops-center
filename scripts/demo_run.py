"""Run a few agent questions and print results. Traces go to OTEL_EXPORTER_OTLP_ENDPOINT.

Usage: uv run --python 3.12 python scripts/demo_run.py
Set AOC_LLM_PROVIDERS=fake (default falls back to fake when no API keys are set) to run offline.
"""

from aoc_runtime.runner import run_agent
from aoc_runtime.telemetry import init_telemetry

QUESTIONS = [
    ("supply-chain-assistant", "What is the escalation rule when stock cover drops below 3 days?"),
    ("supply-chain-assistant", "What is the current stock and demand for SKU-1001?"),
    ("supply-chain-assistant", "What is the standard lead time for imported ingredients?"),
    ("hr-policy-bot", "How many days of paid sick leave do employees get?"),
    ("hr-policy-bot", "What is the deadline for submitting an expense claim?"),
]

if __name__ == "__main__":
    provider = init_telemetry("aoc-agents")
    for agent, q in QUESTIONS:
        r = run_agent(agent, q)
        print(f"{r.run_id[:8]} {agent} {r.status} steps={r.steps} tools={r.tool_calls} "
              f"tokens={r.input_tokens}/{r.output_tokens} cost=${r.cost_usd:.6f}")
        print("  ", r.answer[:160].replace("\n", " "))
    provider.force_flush()
