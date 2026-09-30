"""Renders the Streamlit pages against stubbed API responses (no servers needed)."""

from types import SimpleNamespace

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from aoc_runtime.config import REPO_ROOT  # noqa: E402
from console.ui import client  # noqa: E402

APP = str(REPO_ROOT / "console" / "ui" / "app.py")

RUN = {
    "run_id": "a" * 32, "agent": "hr-policy-bot", "version": "1.0.0", "tenant": "snackco-hr",
    "app_id": "hr", "environment": "dev", "status": "loop_stopped", "loop_reason": "repeat",
    "model": "fake-llm", "steps": 2, "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.0001,
    "latency_s": 0.2, "started_at": "2026-09-30T10:00:00+00:00", "trace_url": "http://x/t",
    "replay_of": None, "replay_mode": None, "question": "q?", "answer": "stopped",
    "step_records": [
        {"idx": 0, "kind": "tool", "name": "rag_search", "input": {"query": "q"}, "output": "boom",
         "status": "error", "latency_ms": 3.0, "input_tokens": 0, "output_tokens": 0},
    ],
}
INCIDENT = {
    "id": 7, "title": "rag_search failing", "severity": "critical", "status": "open",
    "source": "alert", "alert_name": "ToolFailureRateHigh", "agent": "hr-policy-bot",
    "version": "1.0.0", "tenant": "snackco-hr", "tool": "rag_search",
    "runbook": "docs/RUNBOOK.md#tool-failures", "root_cause": "", "firings": 2,
    "created_at": "2026-09-30T10:00:00+00:00", "resolved_at": None, "run_count": 1,
    "runs": [{"run_id": RUN["run_id"], "reason": "failed tool step (rag_search)"}],
    "events": [{"ts": "2026-09-30T10:00:01+00:00", "kind": "opened", "actor": "system",
                "message": "opened by alert"}],
}
FINOPS = {
    "group_by": "tenant", "days": 30, "total_cost_usd": 0.002, "replay_overhead_usd": 0.0,
    "rows": [{"key": "snackco-hr", "runs": 3, "input_tokens": 90, "output_tokens": 30,
              "cost_usd": 0.002, "avg_cost_per_run_usd": 0.0006, "share": 1.0}],
}


@pytest.fixture(autouse=True)
def stub_api(monkeypatch):
    def fake_call(method, path, **kwargs):
        routes = {
            "/agents": [{"name": "hr-policy-bot", "versions": 1, "prod": "1.0.0", "staging": None}],
            "/stats/versions": [{"agent": "hr-policy-bot", "version": "1.0.0", "runs": 1,
                                 "success_rate": 0.0, "avg_latency_s": 0.2,
                                 "avg_cost_usd": 0.0001, "loops": 1}],
            "/runs": [RUN],
            "/incidents": [INCIDENT],
            "/incidents/7": INCIDENT,
            "/finops/summary": FINOPS,
            "/finops/prices": [
                {"model": "fake-llm", "input_per_m_usd": 0.1, "output_per_m_usd": 0.4}
            ],
            "/finops/whatif": {
                "model": "fake-llm", "current_cost_usd": 0.002, "alt_cost_usd": 0.001,
                "savings_pct": 0.5, "caveat": "Cost only.",
            },
            "/audit": [{"id": 1, "ts": "t", "action": "run", "decision": "allow", "agent": "a",
                        "version": "1", "tenant": "t", "run_id": None, "detail": {}, "hash": "h"}],
            "/audit/verify": {"ok": True, "first_bad_event": None},
        }
        if path.startswith("/runs/") and path.endswith("/replays"):
            return []
        if path.startswith("/runs/"):
            return RUN
        return routes.get(path, [])

    monkeypatch.setattr(client, "_call", fake_call)
    monkeypatch.setattr(client, "_raw", lambda *a, **k: SimpleNamespace(text="tenant,runs\n"))


def open_page(name: str):
    at = AppTest.from_file(APP, default_timeout=30).run()
    if name != "Overview":
        at.sidebar.radio[0].set_value(name).run()
    assert not at.exception, at.exception
    return at


def test_overview_renders_and_flags_open_incidents():
    at = open_page("Overview")
    assert at.header[0].value == "Fleet overview"
    assert any("open incident" in w.value for w in at.warning)


def test_runs_page_shows_run_detail_with_failed_step_and_replay_panel():
    at = open_page("Runs")
    assert any("Step timeline" in m.value for m in at.markdown)
    assert any("❌" in e.label for e in at.expander)
    assert any(s.label == "Replay" for s in at.button)


def test_incidents_page_shows_timeline_and_actions():
    at = open_page("Incidents")
    assert at.header[0].value == "Incidents"
    text = " ".join(m.value for m in at.markdown)
    assert "docs/RUNBOOK.md#tool-failures" in text and "opened" in text
    assert {b.label for b in at.button} >= {"Acknowledge", "Resolve"}


def test_finops_and_audit_pages_render():
    assert open_page("FinOps").header[0].value == "FinOps: showback"
    audit = open_page("Audit")
    assert any("verified" in s.value for s in audit.success)
