"""Dashboards, alert rules and compose config must only reference metrics the code emits."""

import json
import re
import subprocess
import sys

import yaml

from aoc_runtime.config import REPO_ROOT
from aoc_runtime.metrics import METRIC_NAMES

DEPLOY = REPO_ROOT / "deploy"
SUFFIXES = ("_bucket", "_sum", "_count")


def _aoc_metrics_in(text: str) -> set[str]:
    found = set()
    for name in re.findall(r"\baoc_[a-z_]+", text):
        for suffix in SUFFIXES:
            name = name.removesuffix(suffix)
        found.add(name)
    return found


def test_dashboard_uses_only_known_metrics_and_covers_required_signals():
    dash = json.loads((DEPLOY / "grafana/dashboards/agent-health.json").read_text())
    exprs = [t["expr"] for p in dash["panels"] for t in p["targets"]]
    assert _aoc_metrics_in(" ".join(exprs)) <= set(METRIC_NAMES)
    joined = " ".join(exprs)
    for required in (
        "aoc_runs_total",  # success rate
        "aoc_run_duration_seconds",  # latency
        "aoc_tool_calls_total",  # tool failure rate
        "aoc_run_cost_usd",  # cost per run
        "aoc_loops_detected_total",  # loop detection
    ):
        assert required in joined


def test_dashboard_json_is_up_to_date_with_generator():
    from scripts import build_dashboard

    committed = json.loads(build_dashboard.OUT.read_text())
    assert committed == build_dashboard.build(), "run scripts/build_dashboard.py"


def test_alert_rules_reference_known_metrics():
    rules = yaml.safe_load((DEPLOY / "prometheus/alerts.yml").read_text())
    alerts = [r for g in rules["groups"] for r in g["rules"]]
    assert {a["alert"] for a in alerts} >= {
        "AgentSuccessRateLow",
        "ToolFailureRateHigh",
        "AgentLatencyP95High",
        "AgentLoopDetected",
        "DailyCostOverBudget",
    }
    for a in alerts:
        assert _aoc_metrics_in(a["expr"]) <= set(METRIC_NAMES), a["alert"]
        assert a["annotations"]["runbook"].startswith("docs/RUNBOOK.md")


def test_compose_is_valid_yaml_with_expected_services():
    compose = yaml.safe_load((DEPLOY / "compose.yaml").read_text())
    expected = {"postgres", "phoenix", "otel-collector", "prometheus", "alertmanager", "grafana"}
    assert expected <= set(compose["services"])


def test_promtool_free_syntax_check_of_scripts():
    for script in ("build_dashboard.py", "generate_traffic.py"):
        subprocess.run(
            [sys.executable, "-m", "py_compile", str(REPO_ROOT / "scripts" / script)], check=True
        )


def test_prometheus_routes_alerts_to_the_console_webhook():
    prom = yaml.safe_load((DEPLOY / "prometheus/prometheus.yml").read_text())
    assert prom["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"] == [
        "alertmanager:9093"
    ]
    am = yaml.safe_load((DEPLOY / "alertmanager/alertmanager.yml").read_text())
    (hook,) = am["receivers"][0]["webhook_configs"]
    assert hook["url"].endswith("/alerts") and hook["send_resolved"] is True
