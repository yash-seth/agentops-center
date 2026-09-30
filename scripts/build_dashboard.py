"""Generate the Grafana agent-health dashboard (dashboards as code).

Usage: uv run --python 3.12 python scripts/build_dashboard.py
Writes deploy/grafana/dashboards/agent-health.json. tests/test_deploy_assets.py checks that every
metric used here exists in aoc_runtime.metrics.
"""

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "deploy/grafana/dashboards/agent-health.json"
DS = {"type": "prometheus", "uid": "prometheus"}
SEL = 'agent=~"$agent",tenant=~"$tenant",env=~"$env"'


def target(expr: str, legend: str = "", ref: str = "A") -> dict:
    return {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref}


def panel(pid: int, title: str, kind: str, x: int, y: int, w: int, h: int, targets: list, **opts):
    unit = opts.pop("unit", "short")
    p = {
        "id": pid, "title": title, "type": kind, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": targets,
        "fieldConfig": {"defaults": {"unit": unit, **opts.pop("defaults", {})}, "overrides": []},
    }
    p.update(opts)
    return p


def variable(name: str, label_values: str) -> dict:
    return {
        "name": name, "label": name, "type": "query", "datasource": DS,
        "query": {"query": f"label_values(aoc_runs_total, {label_values})", "refId": name},
        "refresh": 2, "includeAll": True, "multi": True, "allValue": ".*",
        "current": {"text": "All", "value": "$__all"},
    }


def build() -> dict:
    success = f'sum(rate(aoc_runs_total{{status="ok",{SEL}}}[$__rate_interval])) / sum(rate(aoc_runs_total{{{SEL}}}[$__rate_interval]))'
    thresholds = {
        "mode": "absolute",
        "steps": [
            {"color": "red", "value": None},
            {"color": "orange", "value": 0.9},
            {"color": "green", "value": 0.97},
        ],
    }
    panels = [
        panel(1, "Success rate", "stat", 0, 0, 6, 5, [target(success)], unit="percentunit",
              defaults={"thresholds": thresholds, "min": 0, "max": 1}),
        panel(2, "Runs per minute", "stat", 6, 0, 6, 5,
              [target(f"sum(rate(aoc_runs_total{{{SEL}}}[$__rate_interval])) * 60")], unit="none"),
        panel(3, "Loops detected (last 1h)", "stat", 12, 0, 6, 5,
              [target(f"sum(increase(aoc_loops_detected_total{{{SEL}}}[1h]))")], unit="none",
              defaults={"thresholds": {"mode": "absolute", "steps": [
                  {"color": "green", "value": None}, {"color": "red", "value": 1}]}}),
        panel(4, "Spend today (list-price USD)", "stat", 18, 0, 6, 5,
              [target(f"sum(increase(aoc_cost_usd_total{{{SEL}}}[1d]))")], unit="currencyUSD"),
        panel(5, "Run latency p50 / p95", "timeseries", 0, 5, 12, 8, [
            target(f"histogram_quantile(0.5, sum by (le) (rate(aoc_run_duration_seconds_bucket{{{SEL}}}[$__rate_interval])))", "p50", "A"),
            target(f"histogram_quantile(0.95, sum by (le) (rate(aoc_run_duration_seconds_bucket{{{SEL}}}[$__rate_interval])))", "p95", "B"),
        ], unit="s"),
        panel(6, "Tool failure rate by tool", "timeseries", 12, 5, 12, 8, [target(
            f'sum by (tool) (rate(aoc_tool_calls_total{{status!="ok",{SEL}}}[$__rate_interval])) '
            f"/ sum by (tool) (rate(aoc_tool_calls_total{{{SEL}}}[$__rate_interval]))", "{{tool}}")],
            unit="percentunit"),
        panel(7, "Cost per run (avg / p95)", "timeseries", 0, 13, 12, 8, [
            target(f"sum(rate(aoc_run_cost_usd_sum{{{SEL}}}[$__rate_interval])) / sum(rate(aoc_run_cost_usd_count{{{SEL}}}[$__rate_interval]))", "avg", "A"),
            target(f"histogram_quantile(0.95, sum by (le) (rate(aoc_run_cost_usd_bucket{{{SEL}}}[$__rate_interval])))", "p95", "B"),
        ], unit="currencyUSD"),
        panel(8, "Tokens per second by model", "timeseries", 12, 13, 12, 8, [target(
            f"sum by (model, direction) (rate(aoc_llm_tokens_total{{{SEL}}}[$__rate_interval]))",
            "{{model}} {{direction}}")], unit="short"),
        panel(9, "Success rate by agent version", "timeseries", 0, 21, 12, 8, [target(
            f'sum by (agent, agent_version) (rate(aoc_runs_total{{status="ok",{SEL}}}[$__rate_interval])) '
            f"/ sum by (agent, agent_version) (rate(aoc_runs_total{{{SEL}}}[$__rate_interval]))",
            "{{agent}} {{agent_version}}")], unit="percentunit"),
        panel(10, "Loops and guardrail blocks", "timeseries", 12, 21, 12, 8, [
            target(f"sum by (reason) (increase(aoc_loops_detected_total{{{SEL}}}[$__rate_interval]))", "loop: {{reason}}", "A"),
            target(f"sum by (type) (increase(aoc_guardrail_blocks_total{{{SEL}}}[$__rate_interval]))", "guardrail: {{type}}", "B"),
        ], unit="none"),
    ]
    return {
        "uid": "aoc-agent-health",
        "title": "Agent health",
        "tags": ["agentops"],
        "schemaVersion": 39,
        "version": 1,
        "refresh": "5s",
        "time": {"from": "now-15m", "to": "now"},
        "templating": {"list": [
            variable("agent", "agent"), variable("tenant", "tenant"), variable("env", "env")]},
        "panels": panels,
    }


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
