
from aoc_runtime import semconv as sc
from aoc_runtime.llm import FakeChatModel
from aoc_runtime.runner import run_agent


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


def test_run_ids_stay_unique_and_valid_when_tracing_is_not_configured(monkeypatch):
    from opentelemetry import trace

    from aoc_runtime import runner

    monkeypatch.setattr(runner, "get_tracer", lambda: trace.NoOpTracer())
    monkeypatch.setenv("AOC_EMBEDDER", "hash")
    ids = {
        run_agent("hr-policy-bot", "sick leave?", llm=FakeChatModel()).run_id for _ in range(3)
    }
    assert len(ids) == 3
    assert all(len(i) == 32 and set(i) != {"0"} for i in ids)
