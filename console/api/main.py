"""Console API process. Run: uv run --python 3.12 uvicorn console.api.main:app --port 8001"""

from aoc_runtime.telemetry import init_telemetry

from .app import create_app

# Replays run agents inside this process, so it needs tracing like the gateway does.
init_telemetry("aoc-console")

app = create_app()
