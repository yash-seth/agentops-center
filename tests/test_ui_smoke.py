"""Renders the Streamlit pages against stubbed API responses (no servers needed)."""

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
    "question": "q?", "answer": "stopped",
    "step_records": [
        {"idx": 0, "kind": "tool", "name": "rag_search", "input": {"query": "q"}, "output": "boom",
         "status": "error", "latency_ms": 3.0, "input_tokens": 0, "output_tokens": 0},
    ],
}


@pytest.fixture(autouse=True)
def stub_api(monkeypatch):
    def fake_call(method, path, **kwargs):
        if path == "/agents":
            return [{"name": "hr-policy-bot", "versions": 1, "prod": "1.0.0", "staging": None}]
        if path == "/stats/versions":
            return [{"agent": "hr-policy-bot", "version": "1.0.0", "runs": 1, "success_rate": 0.0,
                     "avg_latency_s": 0.2, "avg_cost_usd": 0.0001, "loops": 1}]
        if path == "/runs":
            return [RUN]
        if path.startswith("/runs/"):
            return RUN
        return []

    monkeypatch.setattr(client, "_call", fake_call)


def test_overview_renders():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert at.header[0].value == "Fleet overview"


def test_runs_page_shows_run_detail_with_failed_step():
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.sidebar.radio[0].set_value("Runs").run()
    assert not at.exception
    assert any("Step timeline" in m.value for m in at.markdown)
    assert any("❌" in e.label for e in at.expander)
