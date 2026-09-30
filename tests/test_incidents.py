from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aoc_runtime import faults, resilience
from aoc_runtime.faults import Fault
from aoc_runtime.llm import FakeChatModel
from console.api.app import create_app
from console.db import memory_session_factory
from gateway.app import create_gateway


@pytest.fixture
def stack(exporter, metric_reader):
    factory = memory_session_factory()
    console = TestClient(create_app(factory))
    gateway = TestClient(create_gateway(factory, llm_factory=FakeChatModel))
    console.post("/registry/sync")
    for agent in ("hr-policy-bot", "supply-chain-assistant"):
        for env in ("staging", "prod"):
            console.post(
                f"/agents/{agent}/versions/1.0.0/promote", json={"environment": env, "actor": "t"}
            )
    return SimpleNamespace(console=console, gateway=gateway)


def ask(stack, question="sick leave?", agent="hr-policy-bot"):
    return stack.gateway.post(f"/v1/agents/{agent}/runs", json={"question": question}).json()


def alert(name="ToolFailureRateHigh", status="firing", **labels):
    base = {
        "alertname": name, "severity": "warning", "agent": "hr-policy-bot", "tool": "rag_search"
    }
    return {
        "alerts": [
            {
                "status": status,
                "labels": base | labels,
                "annotations": {
                    "summary": f"{name} on hr-policy-bot", "runbook": "docs/RUNBOOK.md#x"
                },
                "fingerprint": "abc123",
            }
        ]
    }


def test_alert_opens_incident_with_failing_runs_attached(stack):
    ask(stack)  # healthy run, must not be attached
    faults.set_faults([Fault(tool="rag_search", error_rate=1.0)])
    bad = [ask(stack)["run_id"] for _ in range(2)]
    faults.clear_faults()
    resilience.reset_breakers()  # 6 failed attempts opened the breaker; start healthy
    ask(stack)

    r = stack.console.post("/alerts", json=alert())
    (inc_id,) = r.json()["incidents"]
    inc = stack.console.get(f"/incidents/{inc_id}").json()

    assert inc["status"] == "open" and inc["severity"] == "warning"
    assert inc["runbook"].startswith("docs/RUNBOOK.md")
    assert {r["run_id"] for r in inc["runs"]} == set(bad)
    assert all("failed tool step" in r["reason"] for r in inc["runs"])
    assert [e["kind"] for e in inc["events"]] == ["opened", "runs_attached"]


def test_loop_alert_attaches_loop_runs_only(stack):
    faults.set_faults([Fault(tool="rag_search", force_loop=True)])
    looped = ask(stack)["run_id"]
    faults.clear_faults()
    ask(stack)
    r = stack.console.post("/alerts", json=alert("AgentLoopDetected", severity="critical", tool=""))
    inc = stack.console.get(f"/incidents/{r.json()['incidents'][0]}").json()
    assert inc["severity"] == "critical"
    assert [(x["run_id"], x["reason"]) for x in inc["runs"]] == [(looped, "loop: repeat")]


def test_refiring_alert_updates_the_same_incident(stack):
    faults.set_faults([Fault(tool="rag_search", error_rate=1.0)])
    ask(stack)
    first = stack.console.post("/alerts", json=alert()).json()["incidents"]
    ask(stack)
    again = stack.console.post("/alerts", json=alert()).json()["incidents"]
    assert first == again
    inc = stack.console.get(f"/incidents/{first[0]}").json()
    assert inc["firings"] == 2 and inc["run_count"] == 2
    assert len(stack.console.get("/incidents").json()) == 1


def test_resolved_alert_adds_timeline_event_but_needs_human_to_close(stack):
    faults.set_faults([Fault(tool="rag_search", error_rate=1.0)])
    ask(stack)
    (inc_id,) = stack.console.post("/alerts", json=alert()).json()["incidents"]
    stack.console.post("/alerts", json=alert(status="resolved"))
    inc = stack.console.get(f"/incidents/{inc_id}").json()
    assert inc["status"] == "open"
    assert inc["events"][-1]["kind"] == "alert_resolved"


def test_triage_workflow_and_validation(stack):
    faults.set_faults([Fault(tool="rag_search", error_rate=1.0)])
    ask(stack)
    (i,) = stack.console.post("/alerts", json=alert()).json()["incidents"]
    c = stack.console

    ack = c.post(f"/incidents/{i}/acknowledge", json={"actor": "yash"})
    assert ack.json()["status"] == "acknowledged"
    assert c.post(f"/incidents/{i}/acknowledge", json={"actor": "yash"}).status_code == 409
    c.post(f"/incidents/{i}/notes", json={"actor": "yash", "message": "index pod restarting"})
    blank = c.post(f"/incidents/{i}/resolve", json={"actor": "yash", "root_cause": " "})
    assert blank.status_code == 409
    done = c.post(
        f"/incidents/{i}/resolve", json={"actor": "yash", "root_cause": "vector index outage"}
    ).json()
    assert done["status"] == "resolved" and done["root_cause"] == "vector index outage"
    kinds = [e["kind"] for e in done["events"]]
    assert kinds == ["opened", "runs_attached", "acknowledged", "note", "resolved"]
    assert c.get("/incidents", params={"status": "open"}).json() == []
    assert c.get("/incidents/999").status_code == 404


def test_manual_incident_and_new_alert_after_resolution_opens_fresh_one(stack):
    faults.set_faults([Fault(tool="rag_search", error_rate=1.0)])
    ask(stack)
    c = stack.console
    body = {"title": "customer report", "severity": "critical", "agent": "hr-policy-bot"}
    manual = c.post("/incidents", json=body).json()
    assert manual["source"] == "manual" and manual["run_count"] == 1
    assert c.post("/incidents", json={"title": "x", "severity": "loud"}).status_code == 409

    (a,) = c.post("/alerts", json=alert()).json()["incidents"]
    c.post(f"/incidents/{a}/resolve", json={"actor": "y", "root_cause": "fixed"})
    (b,) = c.post("/alerts", json=alert()).json()["incidents"]
    assert b != a


def test_audit_endpoints_expose_chain_status(stack):
    ask(stack)
    assert stack.console.get("/audit/verify").json() == {"ok": True, "first_bad_event": None}
    events = stack.console.get("/audit", params={"action": "run"}).json()
    assert len(events) == 1 and events[0]["decision"] == "allow"
