import pytest
from fastapi.testclient import TestClient

from aoc_runtime import faults, resilience
from aoc_runtime.config import get_settings
from aoc_runtime.llm import FakeChatModel
from console.api.app import create_app
from console.db import memory_session_factory
from gateway.app import create_gateway
from scripts.demo import DemoError, run_demo


def make_stack(monkeypatch, chaos_enabled=True):
    monkeypatch.setenv("AOC_ENABLE_CHAOS_API", "true" if chaos_enabled else "false")
    get_settings.cache_clear()
    factory = memory_session_factory()
    console = TestClient(create_app(factory, llm_factory=FakeChatModel))
    gateway = TestClient(create_gateway(factory, llm_factory=FakeChatModel))
    return console, gateway


@pytest.fixture(autouse=True)
def _clean():
    yield
    faults.clear_faults()
    resilience.reset_breakers()
    get_settings.cache_clear()


def test_the_whole_incident_story_runs_end_to_end(exporter, metric_reader, monkeypatch):
    console, gateway = make_stack(monkeypatch)
    lines: list[str] = []
    # Waiting out the breaker cooldown is simulated by resetting it whenever the demo sleeps.
    run_demo(console, gateway, out=lines.append, sleep=lambda _s: resilience.reset_breakers())
    text = "\n".join(lines)

    assert "identical trajectory: True" in text
    assert "recovered: tool calls are succeeding again" in text
    assert "hash chain verified: True" in text and text.rstrip().endswith("Demo complete.")
    assert "rejected by the circuit breaker" in text
    (incident,) = console.get("/incidents").json()
    assert incident["status"] == "resolved" and "dependency outage" in incident["root_cause"]
    # the alert is scoped to hr-policy-bot, so only its 3 outage runs are attached
    assert incident["run_count"] == 3
    attached = console.get(f"/incidents/{incident['id']}").json()["runs"]
    agents = {console.get(f"/runs/{r['run_id']}").json()["agent"] for r in attached}
    assert agents == {"hr-policy-bot"}
    kinds = [e["kind"] for e in console.get(f"/incidents/{incident['id']}").json()["events"]]
    assert kinds == ["opened", "runs_attached", "acknowledged", "resolved"]
    assert faults.get_faults() == []  # the demo cleans up after itself


def test_demo_explains_how_to_fix_a_disabled_chaos_api(exporter, metric_reader, monkeypatch):
    console, gateway = make_stack(monkeypatch, chaos_enabled=False)
    with pytest.raises(DemoError, match="AOC_ENABLE_CHAOS_API=true"):
        run_demo(console, gateway, out=lambda _l: None)


def test_demo_refuses_to_start_on_an_already_unhealthy_stack(exporter, metric_reader, monkeypatch):
    console, gateway = make_stack(monkeypatch)
    faults.set_faults([faults.Fault("rag_search", error_rate=1.0)])
    with pytest.raises(DemoError, match="already has failures"):
        run_demo(console, gateway, out=lambda _l: None)


def test_demo_fails_loudly_if_the_service_never_recovers(exporter, metric_reader, monkeypatch):
    console, gateway = make_stack(monkeypatch)
    with pytest.raises(DemoError, match="did not recover"):
        run_demo(console, gateway, out=lambda _l: None, recovery_timeout_s=0, sleep=lambda _s: None)
