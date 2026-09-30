"""Static checks for the GitHub Actions workflows and repo hygiene (no GitHub needed)."""

import re
import shutil
import subprocess

import pytest
import yaml

from aoc_runtime.config import REPO_ROOT

WORKFLOWS = {p.stem: p for p in (REPO_ROOT / ".github" / "workflows").glob("*.yml")}
TARGETS = re.findall(
    r"^FROM .+ AS (\S+)$", (REPO_ROOT / "deploy/docker/Dockerfile").read_text(), re.M
)
APP_TARGETS = {"gateway", "console-api", "console-ui"}


def load(name: str) -> dict:
    wf = yaml.safe_load(WORKFLOWS[name].read_text())
    wf["on"] = wf.pop(True, wf.get("on"))  # YAML 1.1 parses the key `on` as boolean True
    return wf


def all_steps(wf: dict):
    for job in wf["jobs"].values():
        yield from job.get("steps", [])


def run_text(wf: dict) -> str:
    return "\n".join(s.get("run", "") for s in all_steps(wf))


def test_expected_workflows_exist():
    assert {"ci", "evals", "release"} <= set(WORKFLOWS)


@pytest.mark.parametrize("name", ["ci", "evals", "release", "deploy-aks"])
def test_workflows_are_least_privilege_and_pinned(name):
    wf = load(name)
    assert "permissions" in wf, "declare permissions explicitly instead of the broad default"
    for step in all_steps(wf):
        uses = step.get("uses")
        if uses:
            assert re.search(r"@v\d+(\.\d+)*$", uses), f"{uses} must be pinned to a version tag"


def test_ci_runs_every_quality_gate():
    ci = load("ci")
    text = run_text(ci)
    for command in ("ruff check", "aoc validate", "pytest", "aoc eval --check", "helm lint",
                    "kubeconform", "smoke_test.py", "git diff --exit-code deploy/"):
        assert command in text, f"ci.yml is missing: {command}"
    assert set(ci["on"]) == {"push", "pull_request"}
    assert "concurrency" in ci
    assert ci["jobs"]["kind-smoke"]["needs"] == ["test", "docker", "helm"]


def test_ci_builds_every_image_target_and_uses_the_locked_dependencies():
    ci = load("ci")
    matrix = ci["jobs"]["docker"]["strategy"]["matrix"]["target"]
    assert set(matrix) == APP_TARGETS <= set(TARGETS)
    assert "--frozen" in run_text(ci)


def test_kind_job_uses_the_repo_files_that_exist():
    ci = load("ci")
    steps = ci["jobs"]["kind-smoke"]["steps"]
    kind = next(s for s in steps if s.get("uses", "").startswith("helm/kind-action"))
    assert (REPO_ROOT / kind["with"]["config"]).exists()
    text = run_text(ci)
    assert "deploy/helm/agentops" in text and (REPO_ROOT / "deploy/helm/agentops").is_dir()
    assert (REPO_ROOT / "scripts/smoke_test.py").exists()
    # NodePorts mapped in deploy/kind/cluster.yaml are the ones the smoke test talks to
    mapped = yaml.safe_load((REPO_ROOT / kind["with"]["config"]).read_text())
    host_ports = {m["hostPort"] for m in mapped["nodes"][0]["extraPortMappings"]}
    assert {8000, 8001} <= host_ports and "localhost:8001" in text and "localhost:8000" in text


def test_evals_workflow_triggers_and_regression_handling():
    wf = load("evals")
    assert {"pull_request", "schedule", "workflow_dispatch"} <= set(wf["on"])
    assert "run-evals" in wf["jobs"]["evals"]["if"]
    steps = wf["jobs"]["evals"]["steps"]
    evals_step = next(s for s in steps if s.get("id") == "evals")
    assert evals_step.get("continue-on-error") is True  # so the PR comment is still posted
    assert any("github-script" in s.get("uses", "") for s in steps)
    last = steps[-1]
    assert "steps.evals.outcome == 'failure'" in last["if"] and "exit 1" in last["run"]
    assert wf["permissions"]["pull-requests"] == "write"
    assert "--check" in run_text(wf)


def test_evals_baselines_referenced_by_workflows_are_valid_paths():
    text = WORKFLOWS["evals"].read_text()
    assert "evals/baseline.fake.json" in text
    assert (REPO_ROOT / "evals/baseline.fake.json").exists()
    # the real-model baseline is created the first time real evals run (runner tolerates missing)
    assert "evals/baseline.real.json" in text


def test_release_publishes_every_target_and_attaches_the_registry_snapshot():
    wf = load("release")
    assert wf["on"]["push"]["tags"] == ["v*"]
    assert wf["permissions"] == {"contents": "write", "packages": "write"}
    matrix = wf["jobs"]["images"]["strategy"]["matrix"]["target"]
    assert set(matrix) == APP_TARGETS
    assert wf["jobs"]["images"]["needs"] == "verify"
    assert wf["jobs"]["registry-snapshot"]["needs"] == "verify"
    text = run_text(wf)
    assert "aoc register --scores evals/out/scores.json --export registry-export" in text
    assert "aoc eval --check" in text


def test_secrets_are_only_used_for_intended_purposes():
    for name, path in WORKFLOWS.items():
        for match in re.findall(r"secrets\.([A-Z_]+)", path.read_text()):
            allowed = {"GITHUB_TOKEN", "GOOGLE_API_KEY"} | (
                {"AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID"}
                if name == "deploy-aks"
                else set()
            )
            assert match in allowed, f"{name}: unexpected secret {match}"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_no_ai_attribution_in_history_or_tracked_files():
    """Commits and tracked files carry no AI co-author or 'generated by' credit lines."""
    pattern = re.compile(r"co-authored-by|generated with", re.I)
    log = subprocess.run(
        ["git", "log", "--format=%B"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if log.returncode == 0:
        assert not pattern.search(log.stdout), "a commit message contains an attribution line"
    files = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.split()
    for f in files:
        if f.endswith((".py", ".md", ".yml", ".yaml", ".toml", ".tmpl", ".txt")) and f != (
            "tests/test_workflows.py"
        ):
            assert not pattern.search((REPO_ROOT / f).read_text(encoding="utf-8")), f
