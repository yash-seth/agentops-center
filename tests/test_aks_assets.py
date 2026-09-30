"""Static safety checks for the AKS files. They cannot prove the scripts work against Azure (that
needs a subscription) but they pin the properties that protect you from surprises and cost."""

import re
import shutil
import subprocess

import pytest
import yaml

from aoc_runtime.config import REPO_ROOT

AKS = REPO_ROOT / "infra" / "aks"
SCRIPTS = ["create-cluster.sh", "deploy.sh", "teardown.sh"]
VALUES = yaml.safe_load((REPO_ROOT / "deploy/helm/agentops/values.yaml").read_text())
VALUES_AKS = yaml.safe_load((REPO_ROOT / "deploy/helm/agentops/values-aks.yaml").read_text())


def working_bash() -> str | None:
    """A bash that can actually run (on Windows the PATH entry is often the WSL launcher)."""
    for candidate in (shutil.which("bash"), "C:/Program Files/Git/bin/bash.exe", "/usr/bin/bash"):
        if not candidate:
            continue
        try:
            probe = subprocess.run([candidate, "-c", "exit 0"], capture_output=True, timeout=20)
            if probe.returncode == 0:
                return candidate
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


BASH = working_bash()


@pytest.mark.skipif(BASH is None, reason="no working bash available")
@pytest.mark.parametrize("script", SCRIPTS)
def test_scripts_are_syntactically_valid_bash(script):
    result = subprocess.run([BASH, "-n", (AKS / script).as_posix()], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", SCRIPTS)
def test_scripts_fail_fast_and_use_lf_endings(script):
    raw = (AKS / script).read_bytes()
    assert b"\r\n" not in raw
    assert raw.startswith(b"#!/usr/bin/env bash")
    assert b"set -euo pipefail" in raw


def test_cluster_is_created_cheap_and_tagged_for_teardown():
    text = (AKS / "create-cluster.sh").read_text()
    assert "--tier free" in text and "--node-count 1" in text
    assert "purpose=agentops-demo" in text  # the tag teardown.sh requires
    assert "read -r -p" in text  # asks before creating billable resources
    for paid_addon in ("monitoring", "defender", "--enable-addons"):
        assert paid_addon not in text


def test_teardown_can_only_delete_the_tagged_demo_group():
    text = (AKS / "teardown.sh").read_text()
    assert "purpose" in text and "agentops-demo" in text and "Refusing to delete" in text
    assert text.index("Refusing to delete") < text.index("az group delete")  # check comes first
    assert 'typed" == "$RG"' in text  # confirmation requires typing the group name
    assert text.count("az group delete") == 1
    # never deletes by wildcard or by listing groups
    assert "az group list" not in text and "xargs" not in text


def test_deploy_uses_the_aks_values_and_the_chart_that_exists():
    text = (AKS / "deploy.sh").read_text()
    assert "values-aks.yaml" in text and "--wait" in text
    assert (REPO_ROOT / "deploy/helm/agentops/values-aks.yaml").exists()
    assert (REPO_ROOT / "scripts/smoke_test.py").exists()


def _leaf_paths(node, prefix=""):
    for key, value in node.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            yield from _leaf_paths(value, path + ".")
        else:
            yield path


def test_aks_values_only_override_keys_that_exist_in_the_chart():
    for path in _leaf_paths(VALUES_AKS):
        node = VALUES
        for part in path.split("."):
            assert isinstance(node, dict) and part in node, f"{path} is not in values.yaml"
            node = node[part]


def test_aks_defaults_are_private_and_not_prod():
    assert VALUES_AKS["service"]["type"] == "ClusterIP"
    assert VALUES_AKS["app"]["env"] != "prod"
    assert VALUES_AKS["app"]["enableChaosApi"] is False


def test_aks_requests_fit_on_one_small_node():
    """One Standard_B2s has 4 GiB; requests must leave room for the system pods."""
    merged = {**VALUES, **VALUES_AKS}

    def mem(node) -> int:
        value = node["resources"]["requests"]["memory"]
        return int(re.match(r"\d+", value).group()) * (1024 if value.endswith("Gi") else 1)

    obs = {**VALUES["observability"], **VALUES_AKS["observability"]}
    total = sum(
        mem(merged[c]) for c in ("gateway", "consoleApi", "consoleUi", "postgres")
    ) + sum(mem(obs[c]) for c in ("phoenix", "collector", "prometheus", "alertmanager", "grafana"))
    assert total < 2400, f"requests total {total} MiB; leave headroom on a 4 GiB node"


def test_deploy_workflow_is_manual_oidc_and_gated():
    wf = yaml.safe_load((REPO_ROOT / ".github/workflows/deploy-aks.yml").read_text())
    triggers = wf.get(True) or wf.get("on")
    assert set(triggers) == {"workflow_dispatch"}  # never runs automatically
    assert wf["permissions"] == {"id-token": "write", "contents": "read"}
    job = wf["jobs"]["deploy"]
    assert job["environment"] == "aks"  # lets you require approvals
    uses = [s.get("uses", "") for s in job["steps"]]
    assert any(u.startswith("azure/login@v") for u in uses)
    text = (REPO_ROOT / ".github/workflows/deploy-aks.yml").read_text()
    assert "--wait" in text and "smoke_test.py" in text
    assert re.findall(r"secrets\.([A-Z_]+)", text) == [
        "AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID"
    ]
