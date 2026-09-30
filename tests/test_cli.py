import json
import shutil
import sys

import pytest
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aoc_runtime import faults
from aoc_runtime.config import REPO_ROOT
from aoc_runtime.llm import FakeChatModel
from cli.aoc import app
from cli.scaffold import ScaffoldError, scaffold_agent
from cli.validate import validate_agent
from console.api.app import create_app
from console.db import memory_session_factory
from gateway.app import create_gateway

runner = CliRunner()


def test_existing_agents_pass_validation():
    result = runner.invoke(app, ["validate"])
    assert result.exit_code == 0
    assert "PASS hr_policy_bot" in result.output and "PASS supply_chain_assistant" in result.output


def test_validate_rejects_unknown_agent():
    assert runner.invoke(app, ["validate", "nope"]).exit_code == 2


@pytest.fixture
def broken(tmp_path):
    """A copy of hr-policy-bot in a temp repo that we can break one field at a time."""
    agent_dir = tmp_path / "agents" / "hr_policy_bot"
    shutil.copytree(REPO_ROOT / "agents" / "hr_policy_bot", agent_dir)
    for sub in ("evals/datasets", "data/docs"):
        shutil.copytree(REPO_ROOT / sub, tmp_path / sub)

    def edit(**changes):
        path = agent_dir / "agent.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for key, value in changes.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return validate_agent(agent_dir, repo_root=tmp_path)

    edit.baseline = lambda: validate_agent(agent_dir, repo_root=tmp_path)
    edit.dir = agent_dir
    edit.root = tmp_path
    return edit


def test_copy_is_valid_before_breaking_it(broken):
    assert broken.baseline() == []


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"tool_timeout": 5}, "unknown key"),
        ({"version": "1.0"}, "semantic"),
        ({"owner": "not-an-email"}, "contact email"),
        ({"model_allowlist": ["gpt-unknown"]}, "price table"),
        ({"model_allowlist": []}, "must not be empty"),
        ({"redaction": {}}, "redaction.policy"),
        ({"limits": {"max_steps": 99}}, "max_steps"),
        ({"limits": {"tool_retrys": 1}}, "unknown limits key"),
        ({"tools": ["rag_search", "ghost_tool"]}, "not implemented"),
        ({"tenant": " "}, "tenant and app_id"),
        ({"owner": None}, "invalid spec"),
    ],
)
def test_validate_catches_common_mistakes(broken, change, expected):
    problems = broken(**change)
    assert any(expected in p for p in problems), problems


def test_validate_requires_an_eval_dataset_and_prompt(broken):
    (broken.root / "evals" / "datasets" / "hr-policy-bot.yaml").unlink()
    (broken.dir / "prompts" / "v1.md").write_text("  ", encoding="utf-8")
    problems = broken.baseline()
    assert any("no eval dataset" in p for p in problems)
    assert any("prompt file" in p for p in problems)


@pytest.fixture
def cleanup_scaffold():
    yield
    for path in (
        REPO_ROOT / "agents" / "claims_helper",
        REPO_ROOT / "data" / "docs" / "claims_helper",
    ):
        shutil.rmtree(path, ignore_errors=True)
    (REPO_ROOT / "evals" / "datasets" / "claims-helper.yaml").unlink(missing_ok=True)
    for name in [m for m in sys.modules if m.startswith("agents.claims_helper")]:
        del sys.modules[name]


def test_new_agent_is_valid_evaluated_and_runnable_from_birth(cleanup_scaffold, exporter):
    result = runner.invoke(app, ["new-agent", "claims-helper", "--template", "tools"])
    assert result.exit_code == 0, result.output
    assert "validates OK" in result.output
    assert (REPO_ROOT / "agents" / "claims_helper" / "agent.yaml").exists()

    assert runner.invoke(app, ["validate", "claims-helper"]).exit_code == 0
    assert runner.invoke(app, ["eval", "--agent", "claims-helper", "--check"]).exit_code == 0

    from aoc_runtime.runner import run_agent

    question = "How fast must urgent issues be escalated?"
    res = run_agent("claims-helper", question, llm=FakeChatModel())
    assert res.status == "ok" and "2 hours" in res.answer
    spec = yaml.safe_load((REPO_ROOT / "agents" / "claims_helper" / "agent.yaml").read_text())
    assert spec["tools"] == ["rag_search", "lookup_status"]
    assert spec["tenant"] == "snackco-claims-helper"


def test_scaffold_refuses_bad_names_templates_and_duplicates(tmp_path):
    with pytest.raises(ScaffoldError, match="kebab"):
        scaffold_agent("Bad_Name", owner="a@b.co", tenant="t", root=tmp_path)
    with pytest.raises(ScaffoldError, match="template"):
        scaffold_agent("ok-name", "magic", owner="a@b.co", tenant="t", root=tmp_path)
    (tmp_path / "agents" / "taken").mkdir(parents=True)
    with pytest.raises(ScaffoldError, match="already exists"):
        scaffold_agent("taken", owner="a@b.co", tenant="t", root=tmp_path)
    assert runner.invoke(app, ["new-agent", "hr-policy-bot"]).exit_code == 2  # exists in the repo


def test_chaos_prints_a_setting_the_runtime_can_load(monkeypatch):
    out = runner.invoke(
        app, ["chaos", "--tool", "rag_search", "--error-rate", "0.5", "--latency", "1.5"]
    ).output
    value = out.splitlines()[0].split("AOC_CHAOS='")[1].rstrip("'")
    monkeypatch.setenv("AOC_CHAOS", value)
    faults.load_from_env()
    assert faults._faults == [faults.Fault("rag_search", 0.5, 1.5, False)]
    assert "AOC_CHAOS=''" in runner.invoke(app, ["chaos", "--clear"]).output


def test_register_imports_agents_with_scores_and_exports_snapshots(tmp_path, monkeypatch):
    factory = memory_session_factory()
    monkeypatch.setattr("console.db.default_session_factory", lambda: factory)
    scores = tmp_path / "scores.json"
    scores.write_text(json.dumps({"hr-policy-bot": {"answers.pass_rate": 1.0}}))
    export = tmp_path / "export"
    result = runner.invoke(
        app, ["register", "--scores", str(scores), "--export", str(export)]
    )
    assert result.exit_code == 0, result.output
    assert "registered hr-policy-bot@1.0.0" in result.output
    snap = json.loads((export / "hr-policy-bot-1.0.0.json").read_text())
    assert snap["eval_scores"] == {"answers.pass_rate": 1.0} and snap["spec"]["tools"]
    assert runner.invoke(app, ["register"]).exit_code == 0  # idempotent


def test_replay_command_prints_the_diff(monkeypatch, exporter, metric_reader):
    factory = memory_session_factory()
    console = TestClient(create_app(factory, llm_factory=FakeChatModel))
    gateway = TestClient(create_gateway(factory, llm_factory=FakeChatModel))
    console.post("/registry/sync")
    for env in ("staging", "prod"):
        console.post(
            "/agents/hr-policy-bot/versions/1.0.0/promote", json={"environment": env, "actor": "t"}
        )
    run_id = gateway.post("/v1/agents/hr-policy-bot/runs", json={"question": "sick leave?"}).json()[
        "run_id"
    ]
    def via_test_client(url, json=None, timeout=None):
        return console.post(url.split("8001")[1], json=json)

    monkeypatch.setattr("httpx.post", via_test_client)
    result = runner.invoke(app, ["replay", run_id])
    assert result.exit_code == 0, result.output
    assert "identical trajectory: True" in result.output
    assert runner.invoke(app, ["replay", "missing"]).exit_code == 1
