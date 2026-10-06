"""End-to-end check of the observability pipeline against a running stack (kind or compose).

  uv run python scripts/e2e_observability.py

Verifies what unit tests cannot: Prometheus scrapes the gateway and loaded the alert rules, Grafana
provisioned its data source and dashboard, traces reach Phoenix, and a real failure drives the whole
alert path (metrics -> Prometheus rule -> Alertmanager -> console webhook -> incident with runs).

Needs the gateway started with AOC_ENABLE_CHAOS_API=true. Phoenix checks are reported but only
fail the script with --strict-phoenix, because its REST surface varies between versions.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable

EXPECTED_ALERTS = {
    "AgentSuccessRateLow", "ToolFailureRateHigh", "AgentLatencyP95High",
    "AgentLoopDetected", "DailyCostOverBudget",
}
AGENT = "hr-policy-bot"


def wait_for(fn: Callable[[], object], timeout_s: float, interval_s: float = 4.0) -> object:
    """Poll until fn returns something truthy; return it, or raise TimeoutError."""
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        try:
            result = fn()
            if result:
                return result
        except Exception as exc:  # noqa: BLE001 - services are still starting
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(interval_s)
    raise TimeoutError(f"condition not met within {timeout_s:.0f}s {last_error}".strip())


def prom_query(prom, expr: str) -> list[dict]:
    r = prom.get("/api/v1/query", params={"query": expr})
    r.raise_for_status()
    return r.json()["data"]["result"]


def run_checks(
    *, console, gateway, prom, grafana, phoenix, alert_timeout_s: float = 420,
    strict_phoenix: bool = False, out=print,
) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, fn: Callable[[], str], required: bool = True) -> None:
        try:
            detail = fn() or ""
            results.append((name, True, detail))
        except Exception as exc:  # noqa: BLE001
            results.append((name, not required, f"{'' if required else 'WARN '}"
                            f"{type(exc).__name__}: {exc}"))
        ok, detail = results[-1][1], results[-1][2]
        out(f"{'PASS' if ok and not detail.startswith('WARN') else 'WARN' if ok else 'FAIL'}  "
            f"{name:34s} {detail}")

    def ask(question: str = "How many days of sick leave?") -> dict:
        r = gateway.post(f"/v1/agents/{AGENT}/runs", json={"question": question})
        r.raise_for_status()
        return r.json()

    # --- setup: make sure an agent is live and has produced some healthy traffic ---
    console.post("/registry/sync").raise_for_status()
    for env in ("staging", "prod"):
        console.post(f"/agents/{AGENT}/versions/1.0.0/promote",
                     json={"environment": env, "actor": "e2e"})
    healthy_runs = [ask() for _ in range(6)]
    trace_id = healthy_runs[0]["run_id"]

    def targets_up() -> str:
        def up():
            data = prom.get("/api/v1/targets").json()["data"]["activeTargets"]
            gw = [t for t in data if t["labels"].get("job") == "aoc-gateway"]
            return gw[0]["health"] == "up" and gw[0]["scrapeUrl"]
        url = wait_for(up, 90)
        return f"gateway scraped at {url}"

    def rules_loaded() -> str:
        groups = prom.get("/api/v1/rules").json()["data"]["groups"]
        names = {r["name"] for g in groups for r in g["rules"] if r["type"] == "alerting"}
        missing = EXPECTED_ALERTS - names
        assert not missing, f"missing alert rules: {sorted(missing)}"
        return f"{len(names)} alert rules loaded"

    def metrics_flow() -> str:
        def has_runs():
            res = prom_query(prom, "sum(aoc_runs_total)")
            return res and float(res[0]["value"][1]) >= 6
        wait_for(has_runs, 90)
        res = prom_query(prom, "sum by (agent, status) (aoc_runs_total)")
        return "; ".join(f"{r['metric']['agent']}/{r['metric']['status']}={r['value'][1]}"
                         for r in res)

    def grafana_ready() -> str:
        health = wait_for(lambda: grafana.get("/api/datasources/uid/prometheus/health").json(), 90)
        assert health.get("status") == "OK", health
        found = grafana.get("/api/search", params={"query": "Agent health"}).json()
        assert any(d.get("uid") == "aoc-agent-health" for d in found), found
        return "datasource healthy, dashboard 'Agent health' provisioned"

    def dashboard_queries_return_data() -> str:
        # The dashboard's core expressions must evaluate against the live metrics.
        ok_expr = 'sum(rate(aoc_runs_total{status="ok"}[2m])) / sum(rate(aoc_runs_total[2m]))'
        wait_for(lambda: prom_query(prom, "sum(aoc_run_duration_seconds_count)"), 60)
        series = {
            "run count": "sum(aoc_run_duration_seconds_count)",
            "cost": "sum(aoc_cost_usd_total)",
            "tokens": "sum(aoc_llm_tokens_total)",
            "tool calls": "sum(aoc_tool_calls_total)",
        }
        got = {k: prom_query(prom, v) for k, v in series.items()}
        empty = [k for k, v in got.items() if not v]
        assert not empty, f"no data for: {empty}"
        _ = prom_query(prom, ok_expr)  # must parse and evaluate (may be empty with a short window)
        return "runs, cost, tokens and tool calls all queryable"

    def phoenix_up() -> str:
        r = wait_for(lambda: phoenix.get("/healthz"), 120)
        assert r.status_code == 200
        projects = phoenix.get("/v1/projects")
        names = []
        if projects.status_code == 200:
            names = [p.get("name") for p in projects.json().get("data", [])]
        return f"healthy; projects={names or 'n/a'}"

    def traces_in_phoenix() -> str:
        def find():
            for project in ("default", "aoc-gateway"):
                r = phoenix.get(f"/v1/projects/{project}/spans", params={"limit": 200})
                if r.status_code == 200:
                    spans = r.json().get("data", [])
                    ids = {s.get("context", {}).get("trace_id") for s in spans}
                    if trace_id in ids:
                        return f"{project}: trace {trace_id[:8]} found among {len(spans)} spans"
            return None
        return str(wait_for(find, 90))

    def alert_path() -> str:
        console.get("/incidents")  # warm up
        before = {i["id"] for i in console.get("/incidents").json()}
        r = gateway.put("/admin/chaos", json={"actor": "e2e", "faults": [
            {"tool": "rag_search", "error_rate": 1.0}]})
        assert r.status_code == 200, f"chaos api: {r.status_code} {r.text}"
        started = time.monotonic()
        try:
            def incident():
                ask()  # keep failures flowing so the rate stays above the threshold
                new = [i for i in console.get("/incidents").json()
                       if i["id"] not in before and i["alert_name"] == "ToolFailureRateHigh"]
                return new[0] if new else None
            inc = wait_for(incident, alert_timeout_s, interval_s=1.0)
        finally:
            gateway.delete("/admin/chaos", params={"actor": "e2e"})
        detail = console.get(f"/incidents/{inc['id']}").json()
        assert detail["runs"], "incident opened but no exemplar runs attached"
        assert detail["runbook"].startswith("docs/RUNBOOK.md"), detail["runbook"]
        assert detail["tool"] == "rag_search", detail["tool"]
        return (f"incident #{inc['id']} opened {time.monotonic() - started:.0f}s after the fault, "
                f"{len(detail['runs'])} runs attached")

    check("prometheus scrapes gateway", targets_up)
    check("prometheus alert rules loaded", rules_loaded)
    check("metrics flow into prometheus", metrics_flow)
    check("grafana datasource + dashboard", grafana_ready)
    check("dashboard queries return data", dashboard_queries_return_data)
    check("phoenix healthy", phoenix_up, required=strict_phoenix)
    check("traces reach phoenix", traces_in_phoenix, required=strict_phoenix)
    check("alert -> incident (real pipeline)", alert_path)
    return results


def main() -> int:
    import httpx

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--console", default="http://localhost:8001")
    ap.add_argument("--gateway", default="http://localhost:8000")
    ap.add_argument("--prometheus", default="http://localhost:9090")
    ap.add_argument("--grafana", default="http://localhost:3000")
    ap.add_argument("--phoenix", default="http://localhost:6006")
    ap.add_argument("--alert-timeout", type=float, default=420)
    ap.add_argument("--strict-phoenix", action="store_true")
    args = ap.parse_args()

    clients = {name: httpx.Client(base_url=url, timeout=30) for name, url in (
        ("console", args.console), ("gateway", args.gateway), ("prom", args.prometheus),
        ("grafana", args.grafana), ("phoenix", args.phoenix))}
    try:
        results = run_checks(**clients, alert_timeout_s=args.alert_timeout,
                             strict_phoenix=args.strict_phoenix)
    finally:
        for c in clients.values():
            c.close()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
