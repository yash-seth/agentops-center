from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aoc_runtime import faults, resilience
from aoc_runtime import semconv as sc
from aoc_runtime.config import REPO_ROOT
from aoc_runtime.faults import Fault
from aoc_runtime.llm import FakeChatModel
from aoc_runtime.spec import load_spec
from console import registry
from console.api.app import create_app
from console.db import memory_session_factory
from gateway.app import create_gateway


@pytest.fixture
def stack(exporter, metric_reader):
    factory = memory_session_factory()
    console = TestClient(create_app(factory, llm_factory=FakeChatModel))
    gateway = TestClient(create_gateway(factory, llm_factory=FakeChatModel))
    console.post("/registry/sync")
    for agent in ("hr-policy-bot", "supply-chain-assistant"):
        for env in ("staging", "prod"):
            console.post(
                f"/agents/{agent}/versions/1.0.0/promote", json={"environment": env, "actor": "t"}
            )
    return SimpleNamespace(console=console, gateway=gateway, factory=factory)


def ask(stack, question="sick leave?", agent="hr-policy-bot"):
    return stack.gateway.post(f"/v1/agents/{agent}/runs", json={"question": question}).json()


def replay(stack, run_id, mode="deterministic", **extra):
    body = {"mode": mode, "actor": "yash", **extra}
    return stack.console.post(f"/runs/{run_id}/replay", json=body)


def test_deterministic_replay_reproduces_a_healthy_run_exactly(stack):
    original = ask(stack, "how many sick days?")
    r = replay(stack, original["run_id"])
    assert r.status_code == 200
    body = r.json()
    assert body["diff"]["identical"] and body["diff"]["answer_equal"]
    assert body["diff"]["cost_usd"]["delta"] == 0
    assert body["run"]["replay_of"] == original["run_id"]
    assert body["run"]["run_id"] != original["run_id"]  # a replay is its own run and trace
    assert [s["kind"] for s in body["run"]["step_records"]] == ["llm", "tool", "llm"]


def test_deterministic_replay_reproduces_a_tool_failure(stack):
    faults.set_faults([Fault(tool="rag_search", error_rate=1.0)])
    original = ask(stack)
    faults.clear_faults()
    resilience.reset_breakers()  # the tool is healthy now; the replay must still fail identically
    body = replay(stack, original["run_id"]).json()
    assert body["diff"]["identical"]
    tool = next(s for s in body["run"]["step_records"] if s["kind"] == "tool")
    assert tool["status"] == "error" and "injected failure" in tool["output"]


def test_deterministic_replay_reproduces_a_loop_stop(stack):
    faults.set_faults([Fault(tool="rag_search", force_loop=True)])
    original = ask(stack)
    faults.clear_faults()
    body = replay(stack, original["run_id"]).json()
    assert original["status"] == "loop_stopped"
    assert body["run"]["status"] == "loop_stopped" and body["run"]["loop_reason"] == "repeat"
    assert body["diff"]["identical"]


def test_replay_does_not_touch_production_metrics_or_breakers(stack, metric_reader):
    original = ask(stack)

    def total(name):
        return sum(
            p.value
            for rm in metric_reader.get_metrics_data().resource_metrics
            for sm in rm.scope_metrics
            for m in sm.metrics
            if m.name == name
            for p in m.data.data_points
        )

    before = (total("aoc_runs"), total("aoc_tool_calls"))
    replay(stack, original["run_id"])
    assert (total("aoc_runs"), total("aoc_tool_calls")) == before


def test_replay_spans_are_tagged(stack, exporter):
    original = ask(stack)
    new_id = replay(stack, original["run_id"]).json()["run"]["run_id"]
    spans = [s for s in exporter.get_finished_spans() if s.attributes[sc.RUN_ID] == new_id]
    assert spans and all(s.attributes[sc.REPLAY_OF] == original["run_id"] for s in spans)


def test_rerun_against_another_version_shows_a_diff(stack):
    spec = load_spec(REPO_ROOT / "agents" / "hr_policy_bot").model_copy(
        update={"version": "1.1.0", "prompt_text": "Answer briefly.", "tools": []}
    )
    with stack.factory() as s:
        registry.register_version(s, spec)
    for env in ("staging", "prod"):
        stack.console.post(
            "/agents/hr-policy-bot/versions/1.1.0/promote", json={"environment": env, "actor": "t"}
        )
    original = ask(stack)  # served by 1.1.0 now (no tools), so run one on 1.0.0 via rollback
    stack.console.post("/agents/hr-policy-bot/rollback", json={"actor": "t"})
    on_v10 = ask(stack)
    assert (original["version"], on_v10["version"]) == ("1.1.0", "1.0.0")

    body = replay(stack, on_v10["run_id"], "rerun", version="1.1.0").json()
    assert body["run"]["version"] == "1.1.0" and body["run"]["replay_mode"] == "rerun"
    assert not body["diff"]["identical"] and body["diff"]["first_divergence"] is not None
    assert body["diff"]["version"] == {"original": "1.0.0", "replay": "1.1.0"}
    kinds = [row["original"]["kind"] if row["original"] else None for row in body["diff"]["steps"]]
    assert "tool" in kinds  # the original used a tool; the new version has none
    assert any(row["replay"] is None for row in body["diff"]["steps"])


def test_rerun_defaults_to_the_live_version(stack):
    original = ask(stack)
    body = replay(stack, original["run_id"], "rerun").json()
    assert body["run"]["version"] == "1.0.0" and body["diff"]["status"]["replay"] == "ok"


def test_replay_validation_and_listing(stack):
    original = ask(stack)
    assert replay(stack, "nope").status_code == 404
    assert replay(stack, original["run_id"], "bogus").status_code == 409
    new_id = replay(stack, original["run_id"]).json()["run"]["run_id"]
    assert replay(stack, new_id).status_code == 409  # cannot replay a replay
    listed = stack.console.get(f"/runs/{original['run_id']}/replays").json()
    assert [r["run_id"] for r in listed] == [new_id]


def test_replay_is_audited(stack):
    original = ask(stack)
    replay(stack, original["run_id"])
    events = stack.console.get("/audit", params={"action": "replay"}).json()
    assert events[0]["detail"]["of"] == original["run_id"]
    assert events[0]["detail"]["actor"] == "yash"
    assert stack.console.get("/audit/verify").json()["ok"] is True


def test_runs_without_captured_content_cannot_be_replayed(stack, monkeypatch):
    from aoc_runtime.config import get_settings

    monkeypatch.setenv("AOC_CAPTURE_CONTENT", "false")
    get_settings.cache_clear()
    original = ask(stack)
    r = replay(stack, original["run_id"])
    assert r.status_code == 409 and "not captured" in r.json()["detail"]
    get_settings.cache_clear()
