"""Static checks for the Helm chart. `helm lint` / `helm template` run in CI; these catch the most
common chart mistakes locally without needing helm or a cluster."""

import re

import yaml

from aoc_runtime.config import REPO_ROOT, Settings
from scripts.sync_helm_files import CHART_FILES, DEPLOY, MAPPING, normalized

CHART = DEPLOY / "helm" / "agentops"
TEMPLATES = sorted((CHART / "templates").glob("*"))
VALUES = yaml.safe_load((CHART / "values.yaml").read_text())
ALL_TEMPLATE_TEXT = "\n".join(t.read_text() for t in TEMPLATES)


def test_chart_metadata():
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
    assert chart["apiVersion"] == "v2" and chart["name"] == "agentops"
    assert re.match(r"^\d+\.\d+\.\d+$", chart["version"])


def test_config_copies_match_their_sources():
    for target, source in MAPPING.items():
        assert normalized(CHART_FILES / target) == normalized(DEPLOY / source), (
            f"{target} drifted from deploy/{source}; run scripts/sync_helm_files.py"
        )


def test_every_values_reference_exists_in_values_yaml():
    refs = set(re.findall(r"\.Values\.([A-Za-z0-9_.]+)", ALL_TEMPLATE_TEXT))
    assert refs, "templates reference no values?"
    for ref in refs:
        node = VALUES
        for part in ref.split("."):
            assert isinstance(node, dict) and part in node, f".Values.{ref} is not in values.yaml"
            node = node[part]


def test_template_control_blocks_are_balanced():
    for path in TEMPLATES:
        text = path.read_text()
        opens = len(re.findall(r"\{\{-?\s*(if|range|with|define)\b", text))
        ends = len(re.findall(r"\{\{-?\s*end\s*-?\}\}", text))
        assert opens == ends, f"{path.name}: {opens} block openers but {ends} 'end'"
        assert text.count("{{") == text.count("}}"), path.name


def test_included_helpers_are_defined():
    defined = set(re.findall(r'define "([^"]+)"', ALL_TEMPLATE_TEXT))
    used = set(re.findall(r'include "([^"]+)"', ALL_TEMPLATE_TEXT))
    assert used <= defined, used - defined


def test_service_names_match_the_hostnames_the_configs_use():
    services = set(re.findall(r"kind: Service\nmetadata:\n  name: (\S+)", ALL_TEMPLATE_TEXT))
    assert {"gateway", "console-api", "console-ui", "postgres", "phoenix", "otel-collector",
            "prometheus", "alertmanager", "grafana"} <= services
    prom = yaml.safe_load((DEPLOY / "prometheus/prometheus.yml").read_text())
    targets = {
        t.split(":")[0]
        for job in prom["scrape_configs"] for c in job["static_configs"] for t in c["targets"]
    }
    assert targets <= services
    am = yaml.safe_load((DEPLOY / "alertmanager/alertmanager.yml").read_text())
    assert am["receivers"][0]["webhook_configs"][0]["url"].split("/")[2].split(":")[0] in services
    collector = yaml.safe_load((DEPLOY / "otel-collector.yaml").read_text())
    phoenix_url = collector["exporters"]["otlphttp/phoenix"]["endpoint"]
    assert phoenix_url.split("//")[1].split(":")[0] in services
    datasource = yaml.safe_load((CHART_FILES / "datasource.yml").read_text())
    assert datasource["datasources"][0]["url"].split("//")[1].split(":")[0] in services


def test_node_ports_are_valid_and_mapped_by_the_kind_config():
    ports = VALUES["service"]["nodePorts"]
    assert len(set(ports.values())) == len(ports)
    assert all(30000 <= p <= 32767 for p in ports.values())
    kind = yaml.safe_load((DEPLOY / "kind/cluster.yaml").read_text())
    mapped = {m["containerPort"] for m in kind["nodes"][0]["extraPortMappings"]}
    assert set(ports.values()) == mapped


def test_app_environment_uses_real_settings():
    helper = (CHART / "templates/_helpers.tpl").read_text()
    app_env = helper.split('define "agentops.appEnv"')[1].split("{{- end -}}")[0]
    names = re.findall(r"- name: (\S+)", app_env)
    settings = {n.upper() for n in Settings.model_fields}
    for name in names:
        assert name == "POSTGRES_PASSWORD" or name in settings, name
    assert names[0] == "POSTGRES_PASSWORD"  # $(POSTGRES_PASSWORD) is only expanded if defined first


def test_workload_ports_match_the_container_ports():
    ports = {"gateway": 8000, "console-api": 8001, "console-ui": 8501}
    for name, port in ports.items():
        text = (CHART / "templates" / f"{name}.yaml").read_text()
        assert f"containerPort: {port}" in text and f"port: {port}" in text


def test_secure_defaults():
    assert VALUES["secrets"]["googleApiKey"] == "" and VALUES["secrets"]["groqApiKey"] == ""
    helper = (CHART / "templates/_helpers.tpl").read_text()
    assert "runAsNonRoot: true" in helper and "allowPrivilegeEscalation: false" in helper
    for name in ("gateway", "console-api", "console-ui"):
        assert "agentops.securityContext" in (CHART / "templates" / f"{name}.yaml").read_text()
    for component in ("gateway", "consoleApi", "consoleUi", "postgres"):
        assert VALUES[component]["resources"]["requests"], component  # scheduling needs requests


def test_smoke_test_and_notes_reference_existing_files():
    notes = (CHART / "templates/NOTES.txt").read_text()
    for path in re.findall(r"(scripts/\S+\.py)", notes):
        assert (REPO_ROOT / path).exists(), path
