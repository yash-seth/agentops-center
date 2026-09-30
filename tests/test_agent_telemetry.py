import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from aoc_runtime import semconv as sc
from aoc_runtime.config import get_settings
from aoc_runtime.llm import FakeChatModel
from aoc_runtime.runner import run_agent
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


def test_run_produces_tagged_spans_and_answer(exporter):
    res = run_agent("supply-chain-assistant", "What is the stock of SKU-1001?", llm=FakeChatModel())
    assert res.status == "ok"
    assert res.tool_calls == ["rag_search", "inventory_sql"]
    assert res.cost_usd > 0
    spans = exporter.get_finished_spans()
    assert spans, "no spans exported"
    names = {s.name for s in spans}
    assert {"agent.run", "tool.rag_search", "tool.inventory_sql"} <= names
    for s in spans:
        for key in sc.REQUIRED_RUN_ATTRIBUTES:
            assert key in s.attributes, f"span {s.name} missing {key}"
        assert s.attributes[sc.RUN_ID] == res.run_id
        assert format(s.context.trace_id, "032x") == res.run_id


def test_tool_failure_is_recorded_not_raised(exporter, monkeypatch):
    from agents.supply_chain_assistant import tools

    def boom():
        raise RuntimeError("index down")

    monkeypatch.setattr(tools, "_store", boom)
    res = run_agent("supply-chain-assistant", "policy question", llm=FakeChatModel())
    assert res.status == "ok"
    tool_spans = [s for s in exporter.get_finished_spans() if s.name == "tool.rag_search"]
    assert tool_spans[0].attributes[sc.TOOL_STATUS] == "error"
