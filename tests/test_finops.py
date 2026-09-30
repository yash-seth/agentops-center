import csv
import io
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from aoc_runtime.cost import cost_usd
from console.api.app import create_app
from console.db import Run, memory_session_factory

NOW = datetime.now(UTC)


def make_run(run_id, *, tenant, agent, model, tin, tout, days_ago=0, replay_of=None):
    return Run(
        run_id=run_id, agent=agent, version="1.0.0", tenant=tenant, app_id="a", environment="dev",
        status="ok", question="q", answer="a", model=model, steps=1, input_tokens=tin,
        output_tokens=tout, cost_usd=cost_usd(model, tin, tout), latency_s=0.1,
        started_at=NOW - timedelta(days=days_ago), replay_of=replay_of,
    )


@pytest.fixture
def client():
    factory = memory_session_factory()
    with factory() as s:
        s.add_all(
            [
                make_run("r1", tenant="snackco-supply", agent="supply-chain-assistant",
                         model="gemini-2.5-flash", tin=1000, tout=200),
                make_run("r2", tenant="snackco-supply", agent="supply-chain-assistant",
                         model="gemini-2.5-flash", tin=2000, tout=400, days_ago=1),
                make_run("r3", tenant="snackco-hr", agent="hr-policy-bot",
                         model="llama-3.3-70b-versatile", tin=500, tout=100),
                make_run("old", tenant="snackco-hr", agent="hr-policy-bot",
                         model="llama-3.3-70b-versatile", tin=500, tout=100, days_ago=60),
                make_run("rp", tenant="snackco-hr", agent="hr-policy-bot",
                         model="llama-3.3-70b-versatile", tin=500, tout=100, replay_of="r3"),
            ]
        )
        s.commit()
    return TestClient(create_app(factory))


def test_summary_by_tenant_excludes_old_runs_and_replays(client):
    body = client.get("/finops/summary", params={"group_by": "tenant", "days": 30}).json()
    rows = {r["key"]: r for r in body["rows"]}
    assert set(rows) == {"snackco-supply", "snackco-hr"}
    assert rows["snackco-supply"]["runs"] == 2 and rows["snackco-hr"]["runs"] == 1
    expected = cost_usd("gemini-2.5-flash", 3000, 600)
    assert rows["snackco-supply"]["cost_usd"] == pytest.approx(expected)
    assert sum(r["share"] for r in body["rows"]) == pytest.approx(1.0)
    replay_cost = cost_usd("llama-3.3-70b-versatile", 500, 100)
    assert body["replay_overhead_usd"] == pytest.approx(replay_cost)


def test_summary_groupings(client):
    by_day = client.get("/finops/summary", params={"group_by": "day"}).json()["rows"]
    assert [r["key"] for r in by_day] == sorted(r["key"] for r in by_day) and len(by_day) == 2
    model_rows = client.get("/finops/summary", params={"group_by": "model"}).json()["rows"]
    assert {r["key"] for r in model_rows} == {"gemini-2.5-flash", "llama-3.3-70b-versatile"}
    assert client.get("/finops/summary", params={"group_by": "planet"}).status_code == 422


def test_showback_csv_is_chargeback_ready(client):
    r = client.get("/finops/showback.csv")
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert [(x["tenant"], x["agent"], x["model"]) for x in rows] == [
        ("snackco-hr", "hr-policy-bot", "llama-3.3-70b-versatile"),
        ("snackco-supply", "supply-chain-assistant", "gemini-2.5-flash"),
    ]
    supply = rows[1]
    assert supply["runs"] == "2" and supply["input_tokens"] == "3000"
    expected = cost_usd("gemini-2.5-flash", 3000, 600)
    assert float(supply["list_price_cost_usd"]) == pytest.approx(expected, abs=1e-6)


def test_whatif_recomputes_cost_with_the_same_tokens(client):
    body = client.get("/finops/whatif", params={"model": "qwen2.5:3b"}).json()  # free local model
    assert body["alt_cost_usd"] == 0 and body["savings_pct"] == pytest.approx(1.0)
    assert body["savings_usd"] == pytest.approx(body["current_cost_usd"])
    assert "quality" in body["caveat"]
    pricier = client.get("/finops/whatif", params={"model": "gemini-2.5-flash"}).json()
    hr = next(r for r in pricier["by_agent"] if r["agent"] == "hr-policy-bot")
    assert hr["alt"] == pytest.approx(cost_usd("gemini-2.5-flash", 500, 100))
    assert client.get("/finops/whatif", params={"model": "gpt-9"}).status_code == 422


def test_price_table_lists_models(client):
    models = {p["model"] for p in client.get("/finops/prices").json()}
    assert {"gemini-2.5-flash", "fake-llm"} <= models
