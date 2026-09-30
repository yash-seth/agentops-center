"""Console API process. Run: uv run --python 3.12 uvicorn console.api.main:app --port 8001"""

from aoc_runtime.config import get_settings
from aoc_runtime.telemetry import init_telemetry

from .. import registry
from ..db import default_session_factory
from .app import create_app

# Replays run agents inside this process, so it needs tracing like the gateway does.
init_telemetry("aoc-console")

app = create_app()

if get_settings().aoc_auto_sync:
    with default_session_factory()() as _session:
        registry.sync_from_repo(_session)  # idempotent; versions are immutable
