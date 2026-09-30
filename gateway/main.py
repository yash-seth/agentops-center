"""Gateway process: telemetry + metrics + /metrics for Prometheus.

Run: uv run --python 3.12 uvicorn gateway.main:app --port 8000
Set AOC_EMBEDDER=hash AOC_LLM_PROVIDERS=fake AOC_TELEMETRY_ENABLED=false to run fully offline.
"""

from prometheus_client import make_asgi_app

from aoc_runtime import faults, metrics
from aoc_runtime.telemetry import init_telemetry

from .app import create_gateway

metrics.init_metrics()
init_telemetry("aoc-gateway")
faults.load_from_env()

app = create_gateway()
app.mount("/metrics", make_asgi_app())
