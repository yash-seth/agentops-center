import time

import pytest

from aoc_runtime import faults
from aoc_runtime import semconv as sc
from aoc_runtime.config import REPO_ROOT
from aoc_runtime.faults import Fault
from aoc_runtime.llm import FakeChatModel
from aoc_runtime.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    is_transient,
    run_with_timeout,
)
from aoc_runtime.runner import run_agent
from aoc_runtime.spec import load_spec


def _hr_spec(**limits):
    spec = load_spec(REPO_ROOT / "agents" / "hr_policy_bot")
    return spec.model_copy(update={"limits": spec.limits.model_copy(update=limits)})


def test_transient_classification():
    assert is_transient(TimeoutError()) and is_transient(ConnectionError())
    assert not is_transient(ValueError("bad args"))


def test_breaker_opens_after_threshold_then_half_opens():
    b = CircuitBreaker(threshold=2, cooldown_s=0.1)
    b.before_call()
    b.record_failure()
    b.before_call()
    b.record_failure()
    assert b.is_open
    with pytest.raises(CircuitOpenError):
        b.before_call()
    time.sleep(0.3)  # generous: Windows timers can wake early
    b.before_call()  # half-open: one trial call allowed
    b.record_success()
    assert not b.is_open


def test_timeout_is_enforced():
    with pytest.raises(TimeoutError):
        run_with_timeout(lambda: time.sleep(0.5), 0.05)
    assert run_with_timeout(lambda: "fine", 1) == "fine"


def test_failing_tool_is_retried_and_each_attempt_is_visible(exporter, metric_reader):
    faults.set_faults([Fault(tool="rag_search", error_rate=1.0)])
    res = run_agent("hr-policy-bot", "sick leave?", llm=FakeChatModel())
    assert res.status == "ok"
    attempts = sorted(
        s.attributes["aoc.tool.attempt"]
        for s in exporter.get_finished_spans()
        if s.name == "tool.rag_search"
    )
    assert attempts == [1, 2, 3]  # 1 try + 2 retries (agent.yaml limits.tool_retries)
    tool_step = next(s for s in res.step_records if s.kind == "tool")
    assert tool_step.status == "error"


def test_retry_recovers_from_a_flaky_tool(exporter, monkeypatch):
    from agents.hr_policy_bot import tools

    calls = {"n": 0}
    real = tools.rag_search.func

    def flaky(query: str) -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("upstream reset")
        return real(query)

    monkeypatch.setattr(tools.rag_search, "func", flaky)
    res = run_agent("hr-policy-bot", "sick leave?", llm=FakeChatModel())
    tool_step = next(s for s in res.step_records if s.kind == "tool")
    assert tool_step.status == "ok" and calls["n"] == 3
    assert "leave_policy.md" in res.answer


def test_non_transient_errors_are_not_retried(exporter, monkeypatch):
    from agents.hr_policy_bot import tools

    calls = {"n": 0}

    def broken(query: str) -> str:
        calls["n"] += 1
        raise ValueError("bad input")

    monkeypatch.setattr(tools.rag_search, "func", broken)
    run_agent("hr-policy-bot", "x", llm=FakeChatModel())
    assert calls["n"] == 1


def test_breaker_opens_across_runs_and_fails_fast(exporter):
    spec = _hr_spec(tool_retries=0, retry_backoff_s=0)
    faults.set_faults([Fault(tool="rag_search", error_rate=1.0)])
    for _ in range(5):
        run_agent("hr-policy-bot", "q", llm=FakeChatModel(), spec=spec)
    faults.clear_faults()  # the tool is healthy again, but the breaker is still open
    res = run_agent("hr-policy-bot", "q", llm=FakeChatModel(), spec=spec)
    tool_step = next(s for s in res.step_records if s.kind == "tool")
    assert tool_step.status == "circuit_open"
    last = [s for s in exporter.get_finished_spans() if s.name == "tool.rag_search"][-1]
    assert last.attributes[sc.TOOL_STATUS] == "circuit_open"
    assert last.attributes["aoc.tool.circuit"] == "open"


def test_timeout_surfaces_as_error_and_is_retried(exporter):
    spec = _hr_spec(tool_timeout_s=0.05, tool_retries=1, retry_backoff_s=0)
    faults.set_faults([Fault(tool="rag_search", latency_s=0.3)])
    res = run_agent("hr-policy-bot", "q", llm=FakeChatModel(), spec=spec)
    tool_step = next(s for s in res.step_records if s.kind == "tool")
    assert tool_step.status == "error" and "exceeded" in tool_step.output
