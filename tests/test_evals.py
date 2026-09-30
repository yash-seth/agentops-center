import json

import pytest

from evals import runner
from evals.runner import Regression, compare, gated_metrics
from rag import chunking

BASELINE = json.loads((runner.REPO_ROOT / "evals" / "baseline.fake.json").read_text())
AGENTS = runner.list_agents()


def test_every_agent_has_a_dataset_and_baseline():
    assert set(AGENTS) == {"hr-policy-bot", "supply-chain-assistant"}
    assert set(AGENTS) <= set(BASELINE["agents"])
    for agent in AGENTS:
        ds = runner.load_dataset(agent)
        assert ds["retrieval"] and ds["trajectory"] and ds["answers"]
        assert (runner.DATA_DIR / "docs" / ds["docs"]).is_dir()


@pytest.mark.parametrize("agent", AGENTS)
def test_agents_meet_their_baseline(agent):
    """The CI gate: deterministic evals must not regress against evals/baseline.fake.json."""
    scores = runner.run_agent_evals(agent, "fake")
    regressions = compare(scores, BASELINE["agents"][agent], BASELINE["tolerance"])
    assert regressions == [], regressions


def test_retrieval_evals_separate_chunking_strategies():
    scores = runner.run_agent_evals("supply-chain-assistant", "fake")
    assert scores["retrieval.heading.mrr"] > scores["retrieval.paragraph.mrr"]


def test_compare_flags_drops_and_respects_tolerance():
    hit = "retrieval.heading.hit@3"
    base = {hit: 1.0, "trajectory.pass_rate": 1.0, "trajectory.loop_rate": 0.0}
    ok = base | {hit: 0.96}
    assert compare(ok, base, 0.05) == []
    bad = base | {hit: 0.5, "trajectory.loop_rate": 0.4}
    found = {r.metric for r in compare(bad, base, 0.05)}
    assert found == {"retrieval.heading.hit@3", "trajectory.loop_rate"}  # lower-is-better handled


def test_compare_ignores_metrics_missing_from_baseline():
    assert compare({"trajectory.pass_rate": 0.0}, {}, 0.05) == []


def test_a_degraded_chunker_makes_the_gate_fail(monkeypatch):
    """Simulates a bad change (tiny chunks destroy context) and checks the eval catches it."""
    tiny = lambda text: chunking.fixed_chunks(text, 25, 0)  # noqa: E731
    monkeypatch.setitem(chunking.STRATEGIES, "heading", tiny)
    scores = runner.run_agent_evals("supply-chain-assistant", "fake")
    regressions = compare(
        scores, BASELINE["agents"]["supply-chain-assistant"], BASELINE["tolerance"]
    )
    assert any(r.metric.startswith("retrieval.heading") for r in regressions)


def test_markdown_report_marks_regressions():
    scores = {"retrieval.heading.hit@3": 0.2, "trajectory.pass_rate": 1.0}
    md = runner.to_markdown(
        {"a": scores}, {"a": {"retrieval.heading.hit@3": 1.0, "trajectory.pass_rate": 1.0}}, 0.05
    )
    assert "❌ regression" in md and "| trajectory.pass_rate | 1.000 | 1.000 | ✅ |" in md


def test_main_exit_code_reflects_the_gate(tmp_path, capsys):
    strict = tmp_path / "strict.json"
    metrics = {m: 1.0 for m in gated_metrics()} | {"trajectory.loop_rate": 0.0}
    # supply answers score below 1.0 with the fake model, so a baseline of 1.0 must fail
    strict.write_text(json.dumps({"tolerance": 0.0, "agents": {"supply-chain-assistant": metrics}}))
    args = ["--agent", "supply-chain-assistant", "--baseline", str(strict), "--check"]
    assert runner.main(args) == 1
    assert "REGRESSION" in capsys.readouterr().err
    assert runner.main(args[:-1]) == 0  # without --check it only reports


def test_outputs_are_written(tmp_path):
    out, md = tmp_path / "scores.json", tmp_path / "report.md"
    args = ["--agent", "hr-policy-bot", "--check", "--out", str(out), "--markdown", str(md)]
    code = runner.main(args)
    assert code == 0
    assert json.loads(out.read_text())["hr-policy-bot"]["answers.pass_rate"] == 1.0
    assert md.read_text().startswith("## Agent evals")


def test_regression_dataclass_is_comparable():
    assert Regression("m", 1.0, 0.5) == Regression("m", 1.0, 0.5)
