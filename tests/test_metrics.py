from aoc_runtime import faults
from aoc_runtime.faults import Fault
from aoc_runtime.llm import FakeChatModel
from aoc_runtime.runner import run_agent


def _collect(reader):
    out: dict[str, list] = {}
    data = reader.get_metrics_data()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                out[metric.name] = list(metric.data.data_points)
    return out


def test_run_emits_labelled_metrics(exporter, metric_reader):
    run_agent("supply-chain-assistant", "stock of SKU-1001?", llm=FakeChatModel())
    m = _collect(metric_reader)

    runs = m["aoc_runs"][0]
    assert runs.value == 1
    assert runs.attributes["status"] == "ok"
    assert runs.attributes["agent"] == "supply-chain-assistant"
    assert runs.attributes["agent_version"] == "1.0.0"
    assert runs.attributes["tenant"] == "snackco-supply"
    assert "run_id" not in runs.attributes  # unbounded cardinality must stay out of labels

    tools = {p.attributes["tool"]: p for p in m["aoc_tool_calls"]}
    assert set(tools) == {"rag_search", "inventory_sql"}
    assert m["aoc_run_duration"][0].count == 1
    assert m["aoc_run_cost_usd"][0].sum > 0
    directions = {p.attributes["direction"] for p in m["aoc_llm_tokens"]}
    assert directions == {"input", "output"}


def test_tool_failures_and_loops_are_countable(exporter, metric_reader):
    faults.set_faults([Fault(tool="rag_search", error_rate=1.0)])
    run_agent("hr-policy-bot", "sick leave?", llm=FakeChatModel())
    faults.set_faults([Fault(tool="rag_search", force_loop=True)])
    run_agent("hr-policy-bot", "sick leave?", llm=FakeChatModel())
    m = _collect(metric_reader)

    failed = [p for p in m["aoc_tool_calls"] if p.attributes["status"] == "error"]
    assert failed and failed[0].value >= 1
    loops = m["aoc_loops_detected"]
    assert loops[0].attributes["reason"] == "repeat" and loops[0].value == 1
    statuses = {p.attributes["status"] for p in m["aoc_runs"]}
    assert statuses == {"ok", "loop_stopped"}


def test_metric_names_match_contract(metric_reader):
    from aoc_runtime import metrics

    assert len(set(metrics.METRIC_NAMES)) == len(metrics.METRIC_NAMES)
