"""Generate agent traffic and serve Prometheus metrics on http://localhost:9464/metrics.

Works fully offline (fake LLM, hash embeddings, no trace export), so the metrics side of the stack
can be exercised without Docker. Point Prometheus at this process (see deploy/prometheus).

Usage:
  uv run --python 3.12 python scripts/generate_traffic.py --runs 200 --interval 0.2
  uv run --python 3.12 python scripts/generate_traffic.py --chaos tool-errors   # spike failures
  uv run --python 3.12 python scripts/generate_traffic.py --chaos loop         # trigger loop guard
"""

import argparse
import os
import random
import time

os.environ.setdefault("AOC_EMBEDDER", "hash")
os.environ.setdefault("AOC_TELEMETRY_ENABLED", "false")
os.environ.setdefault("AOC_LLM_PROVIDERS", "fake")

from prometheus_client import start_http_server  # noqa: E402

from aoc_runtime import faults, metrics  # noqa: E402
from aoc_runtime.faults import Fault  # noqa: E402
from aoc_runtime.runner import run_agent  # noqa: E402
from aoc_runtime.telemetry import init_telemetry  # noqa: E402

TRAFFIC = [
    ("supply-chain-assistant", "What is the stock and demand for SKU-1001?"),
    ("supply-chain-assistant", "When must a P1 ticket be opened for a stock-out?"),
    ("supply-chain-assistant", "What is the lead time for imported ingredients?"),
    ("hr-policy-bot", "How many days of sick leave do employees get?"),
    ("hr-policy-bot", "What is the deadline for expense claims?"),
]

CHAOS = {
    "tool-errors": [Fault(tool="inventory_sql", error_rate=0.6), Fault("rag_search", 0.3)],
    "latency": [Fault(tool="*", latency_s=0.5)],
    "loop": [Fault(tool="rag_search", force_loop=True)],
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--interval", type=float, default=0.2)
    ap.add_argument("--port", type=int, default=9464)
    ap.add_argument("--chaos", choices=sorted(CHAOS), help="inject a fault for the whole run")
    ap.add_argument("--chaos-after", type=int, default=0, help="start chaos after N runs")
    ap.add_argument("--keep-serving", action="store_true", help="keep /metrics up after the runs")
    args = ap.parse_args()

    metrics.init_metrics()
    init_telemetry("aoc-traffic", instrument_langchain=False)
    start_http_server(args.port)
    print(f"metrics on http://localhost:{args.port}/metrics")

    rnd = random.Random(7)
    for i in range(args.runs):
        if args.chaos and i == args.chaos_after:
            faults.set_faults(CHAOS[args.chaos], seed=1)
            print(f"[run {i}] injecting chaos: {args.chaos}")
        agent, question = rnd.choice(TRAFFIC)
        result = run_agent(agent, question)
        print(f"{i:4d} {agent:24s} {result.status:12s} {result.latency_s * 1000:6.0f}ms "
              f"${result.cost_usd:.6f}")
        time.sleep(args.interval)
    if args.keep_serving:
        print("done; serving metrics until Ctrl+C")
        while True:
            time.sleep(60)


if __name__ == "__main__":
    main()
