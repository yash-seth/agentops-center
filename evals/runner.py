"""Eval runner: retrieval quality, agent trajectories and answers, gated against a baseline.

  python -m evals.runner --agent all --llm fake --baseline evals/baseline.fake.json --check

Modes
  fake  deterministic: hash embeddings + the scripted fake model. Free, no keys, runs in every PR.
        It gates retrieval quality and agent plumbing (tools called, step budget, no loops).
  real  configured providers (Gemini/Groq) and real embeddings. Measures actual answer quality;
        needs API keys, so it runs on demand and nightly rather than on every PR.

A run fails (--check) when any gated metric is worse than the baseline by more than the tolerance.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from aoc_runtime.config import DATA_DIR, REPO_ROOT, get_settings
from rag.chunking import STRATEGIES
from rag.indexer import DEFAULT_STRATEGY, build_store

DATASETS = REPO_ROOT / "evals" / "datasets"
DEFAULT_TOLERANCE = 0.05
RETRIEVAL_K = 3


def gated_metrics() -> list[str]:
    return [
        f"retrieval.{DEFAULT_STRATEGY}.hit@{RETRIEVAL_K}",
        f"retrieval.{DEFAULT_STRATEGY}.mrr",
        "trajectory.pass_rate",
        "answers.pass_rate",
        "trajectory.loop_rate",
    ]


LOWER_IS_BETTER = {"trajectory.loop_rate"}


@dataclass(frozen=True)
class Regression:
    metric: str
    baseline: float
    actual: float


def list_agents() -> list[str]:
    return sorted(p.stem for p in DATASETS.glob("*.yaml"))


def load_dataset(agent: str) -> dict:
    path = DATASETS / f"{agent}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no eval dataset for {agent!r} at {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ retrieval
def evaluate_retrieval(dataset: dict, strategy: str, embedder, k: int = RETRIEVAL_K) -> dict:
    docs_dir = DATA_DIR / "docs" / dataset["docs"]
    store = build_store(docs_dir, strategy=strategy, embedder=embedder, backend="local")
    items = dataset["retrieval"]
    hit_at_1 = hit_at_k = 0
    reciprocal = 0.0
    for item in items:
        needle = item["must_contain"].lower()
        rank = next(
            (
                i
                for i, hit in enumerate(store.search(item["query"], k=k))
                if hit.source == item["relevant"] and needle in hit.text.lower()
            ),
            None,
        )
        if rank is not None:
            hit_at_k += 1
            reciprocal += 1 / (rank + 1)
            hit_at_1 += rank == 0
    n = len(items)
    return {"hit@1": hit_at_1 / n, f"hit@{k}": hit_at_k / n, "mrr": reciprocal / n}


# ------------------------------------------------------------------ trajectories / answers
def evaluate_trajectories(agent: str, items: list[dict], llm_factory: Callable) -> dict:
    from aoc_runtime.runner import run_agent

    passed = loops = steps = 0
    for item in items:
        res = run_agent(agent, item["question"], llm=llm_factory())
        used = set(res.tool_calls)
        ok = (
            res.status == "ok"
            and set(item.get("expect_tools", [])) <= used
            and not (set(item.get("forbid_tools", [])) & used)
            and res.steps <= item.get("max_steps", 6)
        )
        passed += ok
        loops += res.loop_reason is not None
        steps += res.steps
    n = len(items)
    return {"pass_rate": passed / n, "loop_rate": loops / n, "avg_steps": steps / n}


def evaluate_answers(agent: str, items: list[dict], llm_factory: Callable) -> dict:
    from aoc_runtime.runner import run_agent

    passed = 0
    for item in items:
        answer = run_agent(agent, item["question"], llm=llm_factory()).answer.lower()
        good = all(s.lower() in answer for s in item.get("must_contain", []))
        bad = any(s.lower() in answer for s in item.get("must_not_contain", []))
        passed += good and not bad
    return {"pass_rate": passed / len(items)}


# ------------------------------------------------------------------ orchestration
def _prepare(llm_mode: str) -> tuple[Callable, object]:
    if llm_mode == "fake":
        os.environ["AOC_EMBEDDER"] = "hash"
        os.environ["AOC_TELEMETRY_ENABLED"] = "false"
        get_settings.cache_clear()
        from aoc_runtime.llm import FakeChatModel
        from rag.embeddings import HashEmbedder

        return FakeChatModel, HashEmbedder()
    if llm_mode == "real":
        get_settings.cache_clear()
        from aoc_runtime.llm import get_llm
        from rag.embeddings import get_embedder

        return get_llm, get_embedder()
    raise ValueError("llm mode must be 'fake' or 'real'")


def run_agent_evals(agent: str, llm_mode: str = "fake") -> dict[str, float]:
    llm_factory, embedder = _prepare(llm_mode)
    dataset = load_dataset(agent)
    scores: dict[str, float] = {}
    for strategy in sorted(STRATEGIES):
        for name, value in evaluate_retrieval(dataset, strategy, embedder).items():
            scores[f"retrieval.{strategy}.{name}"] = value
    for name, value in evaluate_trajectories(agent, dataset["trajectory"], llm_factory).items():
        scores[f"trajectory.{name}"] = value
    for name, value in evaluate_answers(agent, dataset["answers"], llm_factory).items():
        scores[f"answers.{name}"] = value
    return {k: round(v, 4) for k, v in scores.items()}


def compare(
    scores: dict[str, float], baseline: dict[str, float], tolerance: float = DEFAULT_TOLERANCE
) -> list[Regression]:
    out = []
    for metric in gated_metrics():
        if metric not in baseline or metric not in scores:
            continue
        base, actual = baseline[metric], scores[metric]
        if metric in LOWER_IS_BETTER:
            worse = actual > base + tolerance
        else:
            worse = actual < base - tolerance
        if worse:
            out.append(Regression(metric, base, actual))
    return out


def to_markdown(results: dict[str, dict], baselines: dict, tolerance: float) -> str:
    lines = ["## Agent evals", ""]
    for agent, scores in results.items():
        base = baselines.get(agent, {})
        failed = {r.metric for r in compare(scores, base, tolerance)}
        lines += [f"### {agent}", "", "| metric | score | baseline | status |", "|---|---|---|---|"]
        for metric in sorted(scores):
            gated = metric in gated_metrics()
            status = "" if not gated else ("❌ regression" if metric in failed else "✅")
            b = f"{base[metric]:.3f}" if metric in base else "-"
            lines.append(f"| {metric} | {scores[metric]:.3f} | {b} | {status} |")
        lines.append("")
    lines.append(f"Gate: a gated metric may not be worse than baseline by more than {tolerance}.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--agent", default="all", help="agent name or 'all'")
    ap.add_argument("--llm", choices=["fake", "real"], default="fake")
    ap.add_argument("--baseline", type=Path, default=REPO_ROOT / "evals" / "baseline.fake.json")
    ap.add_argument("--out", type=Path, help="write scores JSON here")
    ap.add_argument("--markdown", type=Path, help="write a markdown report here")
    ap.add_argument("--check", action="store_true", help="exit 1 on regression")
    ap.add_argument("--update-baseline", action="store_true", help="overwrite baseline with scores")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        # reports contain emoji; Windows consoles default to cp1252
        sys.stdout.reconfigure(encoding="utf-8")

    agents = list_agents() if args.agent == "all" else [args.agent]
    results = {agent: run_agent_evals(agent, args.llm) for agent in agents}

    baseline_doc = (
        json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline.exists() else {}
    )
    tolerance = baseline_doc.get("tolerance", DEFAULT_TOLERANCE)
    baselines = baseline_doc.get("agents", {})

    if args.update_baseline:
        baseline_doc = {"tolerance": tolerance, "mode": args.llm, "agents": baselines | results}
        args.baseline.write_text(json.dumps(baseline_doc, indent=2) + "\n", encoding="utf-8")
        print(f"baseline updated: {args.baseline}")

    report = to_markdown(results, baseline_doc.get("agents", {}), tolerance)
    print(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(report, encoding="utf-8")

    regressions = {a: compare(s, baselines.get(a, {}), tolerance) for a, s in results.items()}
    bad = {a: r for a, r in regressions.items() if r}
    for agent, items in bad.items():
        for r in items:
            print(f"REGRESSION {agent} {r.metric}: baseline {r.baseline} -> {r.actual}",
                  file=sys.stderr)
    return 1 if (args.check and bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
