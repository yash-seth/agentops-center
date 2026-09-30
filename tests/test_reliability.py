from aoc_runtime import faults
from aoc_runtime import semconv as sc
from aoc_runtime.faults import Fault
from aoc_runtime.llm import FakeChatModel
from aoc_runtime.loop_guard import LoopGuard, args_hash
from aoc_runtime.runner import run_agent


def test_args_hash_ignores_key_order_and_case():
    assert args_hash({"a": "X", "b": 1}) == args_hash({"b": 1, "a": "x"})


def test_guard_flags_repeated_identical_calls():
    g = LoopGuard()
    assert g.observe_call("t", {"q": "a"}) is None
    assert g.observe_call("t", {"q": "a"}) is None
    assert g.observe_call("t", {"q": "a"}) == "repeat"


def test_guard_allows_same_tool_with_different_args():
    g = LoopGuard()
    assert all(g.observe_call("t", {"q": i}) is None for i in range(6))


def test_guard_flags_oscillation():
    g = LoopGuard(max_repeats=99)
    reasons = [g.observe_call(t, {}) for t in ("a", "b", "a", "b")]
    assert reasons[-1] == "oscillation"


def test_guard_step_and_cost_budgets():
    g = LoopGuard(max_steps=2, cost_budget_usd=0.01)
    assert [g.observe_step() for _ in range(3)] == [None, None, "step_budget"]
    assert g.observe_cost(0.02) == "cost_budget"
    assert g.observe_cost(0.001) is None


def test_forced_loop_is_caught_and_stops_gracefully(exporter):
    faults.set_faults([Fault(tool="rag_search", force_loop=True)])
    res = run_agent("hr-policy-bot", "sick leave?", llm=FakeChatModel())
    assert res.status == "loop_stopped"
    assert res.loop_reason == "repeat"
    assert "stopped itself" in res.answer
    root = next(s for s in exporter.get_finished_spans() if s.name == "agent.run")
    assert root.attributes[sc.LOOP_DETECTED] is True
    assert root.attributes[sc.RUN_STATUS] == "loop_stopped"


def test_injected_tool_errors_are_recorded(exporter):
    faults.set_faults([Fault(tool="rag_search", error_rate=1.0)])
    res = run_agent("hr-policy-bot", "sick leave?", llm=FakeChatModel())
    assert res.status == "ok"  # the agent survives; the failure is data
    spans = [s for s in exporter.get_finished_spans() if s.name == "tool.rag_search"]
    assert spans[0].attributes[sc.TOOL_STATUS] == "error"
    assert "injected failure" in spans[0].events[0].attributes["exception.message"]


def test_fault_latency_is_applied(exporter):
    faults.set_faults([Fault(tool="*", latency_s=0.2)])
    res = run_agent("hr-policy-bot", "sick leave?", llm=FakeChatModel())
    assert res.latency_s >= 0.2


def test_chaos_env_var_loads(monkeypatch):
    monkeypatch.setenv("AOC_CHAOS", '[{"tool": "inventory_sql", "error_rate": 0.5}]')
    faults.load_from_env()
    assert faults._faults == [Fault(tool="inventory_sql", error_rate=0.5)]


def test_run_without_faults_is_clean(exporter):
    res = run_agent("hr-policy-bot", "sick leave?", llm=FakeChatModel())
    assert res.status == "ok" and res.loop_reason is None
