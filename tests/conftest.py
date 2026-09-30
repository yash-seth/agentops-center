import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from aoc_runtime import faults, metrics
from aoc_runtime.config import get_settings
from aoc_runtime.telemetry import init_telemetry, reset_telemetry_for_tests


@pytest.fixture
def exporter(monkeypatch):
    monkeypatch.setenv("AOC_EMBEDDER", "hash")
    get_settings.cache_clear()
    reset_telemetry_for_tests()
    exp = InMemorySpanExporter()
    init_telemetry(exporter=exp, simple=True, instrument_langchain=True)
    yield exp
    reset_telemetry_for_tests()
    get_settings.cache_clear()


@pytest.fixture
def metric_reader():
    metrics.reset_metrics_for_tests()
    reader = InMemoryMetricReader()
    metrics.init_metrics(reader)
    yield reader
    metrics.reset_metrics_for_tests()


@pytest.fixture(autouse=True)
def _no_faults():
    faults.clear_faults()
    yield
    faults.clear_faults()
