from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aoc_runtime.config import REPO_ROOT
from aoc_runtime.llm import FakeChatModel
from aoc_runtime.spec import load_spec
from console import registry
from console.api.app import create_app
from console.db import memory_session_factory
from gateway.app import create_gateway


@pytest.fixture
def stack(exporter, metric_reader):
    factory = memory_session_factory()
    console = TestClient(create_app(factory))
    gateway = TestClient(create_gateway(factory, llm_factory=FakeChatModel))
    console.post("/registry/sync")
    return SimpleNamespace(console=console, gateway=gateway, factory=factory)


def _go_live(console, agent="hr-policy-bot", version="1.0.0"):
    for env in ("staging", "prod"):
        r = console.post(
            f"/agents/{agent}/versions/{version}/promote",
            json={"environment": env, "actor": "yash"},
        )
        assert r.status_code == 200, r.text


def _ask(gateway, question="sick leave?", agent="hr-policy-bot"):
    return gateway.post(f"/v1/agents/{agent}/runs", json={"question": question})


def test_gateway_refuses_until_a_version_is_promoted(stack):
    assert _ask(stack.gateway).status_code == 409


def test_run_is_recorded_with_steps_and_trace_link(stack):
    _go_live(stack.console)
    r = _ask(stack.gateway, "sick leave days?")
    assert r.status_code == 200
    run = r.json()
    assert run["status"] == "ok" and run["version"] == "1.0.0"
    assert run["run_id"] in run["trace_url"]

    detail = stack.console.get(f"/runs/{run['run_id']}").json()
    assert [s["kind"] for s in detail["step_records"]] == ["llm", "tool", "llm"]
    tool = detail["step_records"][1]
    assert tool["name"] == "rag_search" and tool["status"] == "ok"
    assert tool["input"]["query"] == "sick leave days?"
    assert tool["latency_ms"] is not None
    assert detail["question"] == "sick leave days?"


def test_promote_changes_served_version_and_rollback_restores_it(stack):
    _go_live(stack.console)
    spec = load_spec(REPO_ROOT / "agents" / "hr_policy_bot").model_copy(
        update={"version": "1.1.0", "prompt_text": "Answer in one sentence."}
    )
    with stack.factory() as s:
        registry.register_version(s, spec)
    _go_live(stack.console, version="1.1.0")

    assert _ask(stack.gateway).json()["version"] == "1.1.0"

    r = stack.console.post("/agents/hr-policy-bot/rollback", json={"actor": "oncall"})
    assert r.status_code == 200 and r.json()["version"] == "1.0.0"
    assert _ask(stack.gateway).json()["version"] == "1.0.0"

    stats = {(s["agent"], s["version"]): s for s in stack.console.get("/stats/versions").json()}
    assert stats[("hr-policy-bot", "1.1.0")]["runs"] == 1
    assert stats[("hr-policy-bot", "1.0.0")]["success_rate"] == 1.0


def test_runs_can_be_filtered_and_missing_run_is_404(stack):
    _go_live(stack.console)
    _ask(stack.gateway)
    console = stack.console
    assert len(console.get("/runs", params={"agent": "hr-policy-bot"}).json()) == 1
    assert console.get("/runs", params={"agent": "supply-chain-assistant"}).json() == []
    assert console.get("/runs", params={"status": "loop_stopped"}).json() == []
    assert console.get("/runs/doesnotexist").status_code == 404


def test_agents_overview_and_invalid_promotion(stack):
    _go_live(stack.console)
    agents = {a["name"]: a for a in stack.console.get("/agents").json()}
    assert agents["hr-policy-bot"]["prod"] == "1.0.0"
    assert agents["supply-chain-assistant"]["prod"] is None
    bad = stack.console.post(
        "/agents/supply-chain-assistant/versions/1.0.0/promote",
        json={"environment": "prod", "actor": "yash"},
    )
    assert bad.status_code == 409
