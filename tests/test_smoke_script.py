from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from aoc_runtime.llm import FakeChatModel
from console.api.app import create_app
from console.db import memory_session_factory
from gateway.app import create_gateway
from scripts.smoke_test import run_smoke, wait_healthy


def test_smoke_checks_pass_against_a_healthy_stack(exporter, metric_reader):
    factory = memory_session_factory()
    console = TestClient(create_app(factory, llm_factory=FakeChatModel))
    gateway = TestClient(create_gateway(factory, llm_factory=FakeChatModel))
    results = run_smoke(console, gateway)
    assert all(ok for _, ok, _ in results), results
    assert [n for n, _, _ in results] == [
        "registry sync", "promote to prod", "governed run", "steps recorded",
        "deterministic replay", "injection blocked", "audit chain",
    ]
    assert all(ok for _, ok, _ in run_smoke(console, gateway))  # safe to re-run


def test_smoke_reports_failures_and_skips_dependents_instead_of_raising(exporter, metric_reader):
    broken = FastAPI()

    @broken.post("/v1/agents/{name}/runs")
    def always_500(name: str):
        raise HTTPException(500, "model provider down")

    console = TestClient(create_app(memory_session_factory(), llm_factory=FakeChatModel))
    results = {n: (ok, d) for n, ok, d in run_smoke(console, TestClient(broken))}

    assert results["registry sync"][0] and results["promote to prod"][0]
    assert not results["governed run"][0] and "500" in results["governed run"][1]
    assert not results["steps recorded"][0] and "skipped" in results["steps recorded"][1]
    assert not results["deterministic replay"][0]
    assert not results["injection blocked"][0]  # a 500 is not the expected 422
    assert results["audit chain"][0]  # independent checks still run


def test_wait_healthy_times_out_with_a_clear_error():
    class Down:
        def get(self, path):
            raise ConnectionError("refused")

    try:
        wait_healthy(Down(), "gateway", timeout_s=0.1)
    except TimeoutError as exc:
        assert "gateway did not become healthy" in str(exc) and "refused" in str(exc)
    else:
        raise AssertionError("expected TimeoutError")
