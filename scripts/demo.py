"""Narrated demo of the incident lifecycle against a running console API and gateway.

  # gateway must allow the chaos toggle (demo only):  AOC_ENABLE_CHAOS_API=true
  uv run python scripts/demo.py --console http://localhost:8001 --gateway http://localhost:8000

Story: healthy traffic -> a dependency fails -> retries and the circuit breaker contain it -> an
alert opens an incident with the affected runs attached -> triage a run -> replay it
deterministically -> the dependency recovers -> verify -> resolve with a root cause -> audit trail
intact.

With the full stack (Prometheus + Alertmanager) the alert arrives on its own; otherwise the demo
posts an Alertmanager-shaped webhook itself (default), so it also works with just two processes.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable

QUESTIONS = {
    "hr-policy-bot": ["How many days of sick leave?", "Expense claim deadline?",
                      "How many weeks of parental leave?"],
    "supply-chain-assistant": ["What is the stock for SKU-1001?", "Lead time for imports?",
                               "When must a P1 ticket be opened?"],
}
ACTOR = "demo"


class DemoError(RuntimeError):
    pass


def run_demo(
    console, gateway, *, out: Callable[[str], None] = print, simulate_alert: bool = True,
    wait_for_alert_s: float = 0, recovery_timeout_s: float = 90, sleep=time.sleep,
) -> None:
    def step(n: int, title: str) -> None:
        out(f"\n[{n}] {title}")

    def ask(agent: str, question: str) -> dict:
        r = gateway.post(f"/v1/agents/{agent}/runs", json={"question": question})
        if r.status_code != 200:
            raise DemoError(f"gateway returned {r.status_code}: {r.text}")
        return r.json()

    def tool_states(run: dict) -> list[str]:
        return [s["status"] for s in run["step_records"] if s["kind"] == "tool"]

    def traffic(label: str) -> list[dict]:
        runs = [ask(a, q) for a, qs in QUESTIONS.items() for q in qs]
        healthy = sum(1 for r in runs if all(s == "ok" for s in tool_states(r)))
        out(f"    {label}: {len(runs)} runs, {healthy} with every tool call ok, "
            f"{len(runs) - healthy} hit tool failures")
        return runs

    step(1, "Registry: sync agents and put version 1.0.0 live")
    console.post("/registry/sync").raise_for_status()
    for agent in QUESTIONS:
        for env in ("staging", "prod"):
            console.post(f"/agents/{agent}/versions/1.0.0/promote",
                         json={"environment": env, "actor": ACTOR})  # 409 if already live: fine
    live = {a["name"]: a["prod"] for a in console.get("/agents").json()}
    out(f"    live versions: {live}")

    step(2, "Healthy traffic")
    baseline = traffic("baseline")
    if any(s != "ok" for r in baseline for s in tool_states(r)):
        raise DemoError("baseline traffic already has failures; restart the gateway and retry")

    step(3, "Incident: the retrieval dependency (rag_search) starts failing")
    r = gateway.put("/admin/chaos", json={"actor": ACTOR, "faults": [
        {"tool": "rag_search", "error_rate": 1.0}]})
    if r.status_code == 404:
        raise DemoError("chaos API is disabled; start the gateway with AOC_ENABLE_CHAOS_API=true")
    r.raise_for_status()
    outage = traffic("during outage")
    states = [s for run in outage for s in tool_states(run)]
    out(f"    tool call outcomes: {states.count('error')} error (with retries), "
        f"{states.count('circuit_open')} rejected by the circuit breaker")
    out("    agents kept answering: "
        f"{sum(1 for x in outage if x['status'] == 'ok')}/{len(outage)} runs returned a response")

    step(4, "Alerting opens an incident")
    incident_id = None
    deadline = time.monotonic() + wait_for_alert_s
    while wait_for_alert_s and time.monotonic() < deadline and incident_id is None:
        open_now = console.get("/incidents", params={"status": "open"}).json()
        incident_id = open_now[0]["id"] if open_now else None
        if incident_id is None:
            sleep(3)
    if incident_id is None and simulate_alert:
        out("    (posting an Alertmanager-shaped webhook; the full stack sends this itself)")
        resp = console.post("/alerts", json={"alerts": [{
            "status": "firing", "fingerprint": "demo-tool-failures",
            "labels": {"alertname": "ToolFailureRateHigh", "severity": "warning",
                       "agent": "hr-policy-bot", "tool": "rag_search"},
            "annotations": {"summary": "Tool rag_search failing for hr-policy-bot (>20%)",
                            "runbook": "docs/RUNBOOK.md#tool-failures"}}]})
        resp.raise_for_status()
        incident_id = resp.json()["incidents"][0]
    if incident_id is None:
        raise DemoError("no incident opened; enable --simulate-alert or wait for Alertmanager")
    incident = console.get(f"/incidents/{incident_id}").json()
    out(f"    incident #{incident['id']} [{incident['severity']}] {incident['title']}")
    out(f"    {len(incident['runs'])} exemplar runs attached automatically; "
        f"runbook: {incident['runbook']}")

    step(5, "Triage: read the failing run's steps")
    run_id = incident["runs"][0]["run_id"]
    run = console.get(f"/runs/{run_id}").json()
    for s in run["step_records"]:
        flag = "" if s["status"] == "ok" else f"   <-- {s['status']}"
        out(f"    {s['idx']}. {s['kind']:4s} {s['name']:12s}{flag}")
    tool = next(s for s in run["step_records"] if s["kind"] == "tool")
    out(f"    root-cause clue: {tool['output'][:90]}")
    out(f"    trace: {run['trace_url']}")

    step(6, "Replay the failing run deterministically (no real tool or model calls)")
    replay = console.post(f"/runs/{run_id}/replay", json={"mode": "deterministic", "actor": ACTOR})
    replay.raise_for_status()
    diff = replay.json()["diff"]
    out(f"    identical trajectory: {diff['identical']}  (new run "
        f"{replay.json()['run']['run_id'][:8]}, excluded from production metrics)")
    if not diff["identical"]:
        raise DemoError("replay did not reproduce the failing run")

    step(7, "Mitigate: the dependency recovers (fault cleared)")
    gateway.delete("/admin/chaos", params={"actor": ACTOR}).raise_for_status()
    out("    the circuit breaker stays open until its cooldown ends, then lets one call probe")
    deadline = time.monotonic() + recovery_timeout_s
    healthy = False
    while time.monotonic() < deadline:
        probe = ask("hr-policy-bot", "How many days of sick leave?")
        if tool_states(probe) == ["ok"]:
            healthy = True
            break
        sleep(5)
        out("    ...waiting for the breaker to close")
    if not healthy:
        raise DemoError("service did not recover within the timeout")
    out("    recovered: tool calls are succeeding again")

    step(8, "Rerun the same question live to confirm the recovery")
    rerun = console.post(f"/runs/{run_id}/replay", json={"mode": "rerun", "actor": ACTOR})
    rerun.raise_for_status()
    rd = rerun.json()
    out(f"    original status {rd['diff']['status']['original']} -> rerun "
        f"{rd['diff']['status']['replay']}; first tool step now "
        f"{next(s for s in rd['run']['step_records'] if s['kind'] == 'tool')['status']}")

    step(9, "Resolve the incident with a root cause")
    console.post(f"/incidents/{incident_id}/acknowledge", json={"actor": ACTOR})
    done = console.post(f"/incidents/{incident_id}/resolve", json={
        "actor": ACTOR,
        "root_cause": "rag_search dependency outage (injected); retries and the circuit breaker "
                      "contained it and the service recovered after the cooldown"})
    done.raise_for_status()
    out(f"    timeline: {[e['kind'] for e in done.json()['events']]}")

    step(10, "Audit trail")
    audit = console.get("/audit/verify").json()
    out(f"    hash chain verified: {audit['ok']}")
    if not audit["ok"]:
        raise DemoError("audit chain is broken")
    out("\nDemo complete.")


def main() -> int:
    import httpx

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--console", default="http://localhost:8001")
    ap.add_argument("--gateway", default="http://localhost:8000")
    ap.add_argument("--no-simulate-alert", action="store_true",
                    help="wait for Alertmanager instead of posting the webhook")
    ap.add_argument("--wait-for-alert", type=float, default=0, help="seconds to wait first")
    args = ap.parse_args()
    with httpx.Client(base_url=args.console, timeout=60) as console, httpx.Client(
        base_url=args.gateway, timeout=60
    ) as gateway:
        try:
            run_demo(console, gateway, simulate_alert=not args.no_simulate_alert,
                     wait_for_alert_s=args.wait_for_alert)
        except DemoError as exc:
            print(f"\nDEMO FAILED: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
