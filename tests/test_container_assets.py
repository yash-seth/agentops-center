"""Static checks for the container files (no Docker needed): they must agree with the code."""

import re

import yaml

from aoc_runtime.config import REPO_ROOT, Settings

DOCKERFILE = (REPO_ROOT / "deploy/docker/Dockerfile").read_text()
COMPOSE = yaml.safe_load((REPO_ROOT / "deploy/compose.yaml").read_text())
SETTINGS = {name.upper() for name in Settings.model_fields}
TARGETS = re.findall(r"^FROM .+ AS (\S+)$", DOCKERFILE, re.M)
APP_SERVICES = {"gateway": 8000, "console-api": 8001, "console-ui": 8501}


def test_dockerfile_defines_one_target_per_deployable():
    assert set(APP_SERVICES) <= set(TARGETS)


def test_dockerfile_is_hardened():
    assert re.search(r"^FROM python:3\.12-slim AS base", DOCKERFILE, re.M)  # pinned, not latest
    assert re.search(r"^USER app$", DOCKERFILE, re.M)  # never run as root
    assert "--frozen" in DOCKERFILE  # builds use exactly the locked dependencies
    assert "--no-dev" in DOCKERFILE
    for target, port in APP_SERVICES.items():
        section = DOCKERFILE.split(f"AS {target}")[1].split("\nFROM ")[0]
        assert f"EXPOSE {port}" in section, target
        assert "HEALTHCHECK" in section, target
        assert "CMD [" in section, target


def test_dockerignore_keeps_secrets_and_local_files_out_of_the_image():
    ignore = (REPO_ROOT / ".dockerignore").read_text().split()
    for path in (".env", ".git", ".venv", "PLAN.md", "CLAUDE.md", "data/*.db"):
        assert path in ignore, path


def test_compose_build_targets_exist_in_the_dockerfile():
    for name, svc in COMPOSE["services"].items():
        if "build" in svc:
            assert svc["build"]["target"] in TARGETS, name
            assert svc["build"]["dockerfile"] == "deploy/docker/Dockerfile"
    assert set(APP_SERVICES) <= {n for n, s in COMPOSE["services"].items() if "build" in s}


def test_compose_env_vars_are_real_settings():
    """A typo such as AOC_DB_URI would be silently ignored at runtime; catch it here."""
    for name in APP_SERVICES:
        env = COMPOSE["services"][name].get("environment", {})
        for key in env:
            if key == "AOC_CONSOLE_URL":  # read by the UI client, not by Settings
                continue
            assert key in SETTINGS, f"{name}: {key} is not a setting in aoc_runtime.config"


def test_compose_dependencies_and_ports_are_consistent():
    services = set(COMPOSE["services"])
    for name, svc in COMPOSE["services"].items():
        deps = svc.get("depends_on", [])
        for dep in deps if isinstance(deps, list) else deps.keys():
            assert dep in services, f"{name} depends on unknown service {dep}"
    for name, port in APP_SERVICES.items():
        assert f"{port}:{port}" in COMPOSE["services"][name]["ports"]
    assert "AOC_AUTO_SYNC" in COMPOSE["services"]["console-api"]["environment"]


def test_monitoring_config_targets_real_compose_services():
    prom = yaml.safe_load((REPO_ROOT / "deploy/prometheus/prometheus.yml").read_text())
    hosts = {
        t.split(":")[0]
        for job in prom["scrape_configs"]
        for cfg in job["static_configs"]
        for t in cfg["targets"]
    }
    hosts |= {t.split(":")[0] for a in prom["alerting"]["alertmanagers"]
              for cfg in a["static_configs"] for t in cfg["targets"]}
    assert {"gateway", "alertmanager"} <= hosts
    for host in hosts:
        assert host in COMPOSE["services"], host  # no dead host-only targets

    am = yaml.safe_load((REPO_ROOT / "deploy/alertmanager/alertmanager.yml").read_text())
    webhook = am["receivers"][0]["webhook_configs"][0]["url"]
    assert re.match(r"http://console-api:8001/alerts$", webhook)


def test_services_share_one_database_and_one_collector():
    for name in ("console-api", "gateway"):
        env = COMPOSE["services"][name]["environment"]
        assert env["AOC_DB_URL"].startswith("postgresql+psycopg://")  # not per-service SQLite
        assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://otel-collector:4318"
