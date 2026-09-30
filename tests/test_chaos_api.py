import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aoc_runtime import faults
from aoc_runtime.config import get_settings
from aoc_runtime.llm import FakeChatModel
from cli.aoc import app
from console import audit
from console.api.app import create_app
from console.db import memory_session_factory
from gateway.app import create_gateway


@pytest.fixture
def stack(exporter, metric_reader, monkeypatch):
    monkeypatch.setenv("AOC_ENABLE_CHAOS_API", "true")
    get_settings.cache_clear()
    factory = memory_session_factory()
    console = TestClient(create_app(factory))
    gateway = TestClient(create_gateway(factory, llm_factory=FakeChatModel))
    console.post("/registry/sync")
    for env in ("staging", "prod"):
        console.post(
            "/agents/hr-policy-bot/versions/1.0.0/promote", json={"environment": env, "actor": "t"}
        )
    yield console, gateway, factory
    get_settings.cache_clear()


def test_chaos_api_is_invisible_unless_enabled(monkeypatch):
    monkeypatch.delenv("AOC_ENABLE_CHAOS_API", raising=False)
    get_settings.cache_clear()
    gateway = TestClient(create_gateway(memory_session_factory()))
    assert gateway.get("/admin/chaos").status_code == 404
    assert gateway.put("/admin/chaos", json={"faults": []}).status_code == 404
    assert gateway.delete("/admin/chaos").status_code == 404


def test_chaos_api_never_works_in_prod(monkeypatch):
    monkeypatch.setenv("AOC_ENABLE_CHAOS_API", "true")
    monkeypatch.setenv("AOC_ENV", "prod")
    get_settings.cache_clear()
    gateway = TestClient(create_gateway(memory_session_factory()))
    assert gateway.get("/admin/chaos").status_code == 404
    get_settings.cache_clear()


def test_faults_can_be_toggled_on_a_running_gateway(stack):
    _, gateway, _ = stack
    ask = lambda: gateway.post(  # noqa: E731
        "/v1/agents/hr-policy-bot/runs", json={"question": "sick leave?"}
    ).json()

    assert all(s["status"] == "ok" for s in ask()["step_records"])
    r = gateway.put(
        "/admin/chaos",
        json={"faults": [{"tool": "rag_search", "error_rate": 1.0}], "actor": "yash"},
    )
    assert r.status_code == 200 and r.json()["faults"][0]["error_rate"] == 1.0
    assert gateway.get("/admin/chaos").json()["faults"][0]["tool"] == "rag_search"
    assert any(s["status"] != "ok" for s in ask()["step_records"])

    assert gateway.delete("/admin/chaos").json() == {"faults": []}
    assert faults.get_faults() == []


def test_chaos_values_are_validated(stack):
    _, gateway, _ = stack
    for bad in ({"error_rate": 1.5}, {"error_rate": -0.1}, {"latency_s": 999}):
        r = gateway.put("/admin/chaos", json={"faults": [{"tool": "x", **bad}]})
        assert r.status_code == 422, bad
    assert faults.get_faults() == []


def test_chaos_changes_are_audited_with_the_actor(stack):
    _, gateway, factory = stack
    gateway.put("/admin/chaos", json={"faults": [{"tool": "*", "latency_s": 1}], "actor": "yash"})
    gateway.delete("/admin/chaos", params={"actor": "yash"})
    with factory() as s:
        events = audit.list_events(s, action="chaos")
        assert audit.verify_chain(s) == (True, None)
    assert [e.detail["actor"] for e in events] == ["yash", "yash"]
    assert events[1].detail["faults"][0]["latency_s"] == 1


def test_cli_applies_and_clears_faults_through_the_api(stack, monkeypatch):
    _, gateway, _ = stack

    def route(method):
        def call(url, params=None, json=None, timeout=None):
            path = "/" + url.split("//")[1].split("/", 1)[1]
            return getattr(gateway, method)(path, **({"json": json} if json else {}),
                                            **({"params": params} if params else {}))
        return call

    monkeypatch.setattr("httpx.put", route("put"))
    monkeypatch.setattr("httpx.delete", route("delete"))
    runner = CliRunner()
    r = runner.invoke(app, ["chaos", "--tool", "rag_search", "--error-rate", "0.5",
                            "--gateway", "http://gw"])
    assert r.exit_code == 0 and "gateway faults now" in r.output
    assert faults.get_faults()[0].error_rate == 0.5
    assert runner.invoke(app, ["chaos", "--clear", "--gateway", "http://gw"]).exit_code == 0
    assert faults.get_faults() == []
