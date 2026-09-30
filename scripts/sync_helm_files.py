"""Copy the monitoring config files into the Helm chart (charts cannot read outside their folder).

  uv run --python 3.12 python scripts/sync_helm_files.py

docker compose and the chart then run the same collector, Prometheus, Alertmanager and Grafana
config. tests/test_helm_chart.py fails if the copies drift.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
CHART_FILES = DEPLOY / "helm" / "agentops" / "files"

# chart file name -> source under deploy/
MAPPING = {
    "otel-collector.yaml": "otel-collector.yaml",
    "prometheus.yml": "prometheus/prometheus.yml",
    "alerts.yml": "prometheus/alerts.yml",
    "alertmanager.yml": "alertmanager/alertmanager.yml",
    "datasource.yml": "grafana/provisioning/datasources/prometheus.yml",
    "dashboards.yml": "grafana/provisioning/dashboards/dashboards.yml",
    "agent-health.json": "grafana/dashboards/agent-health.json",
}


def normalized(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def main() -> None:
    CHART_FILES.mkdir(parents=True, exist_ok=True)
    for target, source in MAPPING.items():
        text = normalized(DEPLOY / source)
        (CHART_FILES / target).write_text(text, encoding="utf-8", newline="\n")
        print(f"synced {source} -> helm/agentops/files/{target}")


if __name__ == "__main__":
    main()
