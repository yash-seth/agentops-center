"""Replay and debug a recorded run.

deterministic  Re-drive the same version's graph with the recorded model responses and tool
               results. Expected to reproduce the original trajectory exactly, so it proves the
               recording is faithful and lets you step through a failure without side effects.
rerun          Re-execute the same question live against another version (default: the live one)
               and diff trajectory, answer, cost and latency: "would the fix have helped?".

Replays are stored as runs linked to the original (replay_of) and are excluded from production
metrics.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from aoc_runtime import faults
from aoc_runtime.replay import (
    REPLAY_RESILIENCE,
    recorded_model,
    recorded_tools,
    step_signature,
)
from aoc_runtime.runner import run_agent

from . import audit, registry, runs
from .db import Run

MODES = ("deterministic", "rerun")
NOT_CAPTURED = "[content not captured]"


class ReplayError(Exception):
    pass


def _steps(run: Run) -> list[dict]:
    return [
        {
            "idx": st.idx, "kind": st.kind, "name": st.name, "input": st.input,
            "output": st.output, "status": st.status, "latency_ms": st.latency_ms,
            "input_tokens": st.input_tokens, "output_tokens": st.output_tokens,
        }
        for st in run.step_records
    ]


def compare(original: Run, replay: Run) -> dict:
    """Side-by-side comparison of two runs' trajectories, answers, cost and latency."""
    a, b = _steps(original), _steps(replay)
    rows = []
    first_divergence = None
    for i in range(max(len(a), len(b))):
        left = a[i] if i < len(a) else None
        right = b[i] if i < len(b) else None
        match = (
            left is not None
            and right is not None
            and step_signature(left) == step_signature(right)
        )
        if not match and first_divergence is None:
            first_divergence = i
        rows.append(
            {
                "idx": i,
                "match": match,
                "original": _brief(left),
                "replay": _brief(right),
            }
        )
    return {
        "identical": first_divergence is None,
        "first_divergence": first_divergence,
        "answer_equal": original.answer == replay.answer,
        "status": {"original": original.status, "replay": replay.status},
        "version": {"original": original.version, "replay": replay.version},
        "cost_usd": {
            "original": original.cost_usd, "replay": replay.cost_usd,
            "delta": replay.cost_usd - original.cost_usd,
        },
        "latency_s": {
            "original": original.latency_s, "replay": replay.latency_s,
            "delta": replay.latency_s - original.latency_s,
        },
        "steps": rows,
    }


def _brief(step: dict | None) -> dict | None:
    if step is None:
        return None
    return {
        "kind": step["kind"], "name": step["name"], "status": step["status"],
        "output": step["output"][:200],
    }


def replay_run(
    s: Session,
    run_id: str,
    *,
    mode: str,
    actor: str = "unknown",
    version: str | None = None,
    llm_factory: Callable[[], object] | None = None,
    redact: Callable[[str], str] = lambda t: t,
) -> tuple[Run, dict]:
    if mode not in MODES:
        raise ReplayError(f"mode must be one of {MODES}")
    original = runs.get_run(s, run_id)
    if original is None:
        raise ReplayError(f"run {run_id} not found")
    if original.replay_of:
        raise ReplayError("replay the original run, not a replay")
    steps = _steps(original)
    if any(st["output"] == NOT_CAPTURED for st in steps) or original.question == NOT_CAPTURED:
        raise ReplayError("content was not captured for this run, so it cannot be replayed")

    if mode == "deterministic":
        row = registry.get_version(s, original.agent, original.version)
        spec = registry.spec_of(row)
        with faults.suspended():
            result = run_agent(
                original.agent, original.question, llm=recorded_model(steps),
                tenant=original.tenant, spec=spec, tools_override=recorded_tools(steps),
                resilience=REPLAY_RESILIENCE, replay_of=run_id, replay_mode=mode,
            )
    else:
        row = (
            registry.get_version(s, original.agent, version)
            if version
            else registry.served_version(s, original.agent, "prod")
        )
        if row is None:
            raise ReplayError(f"{original.agent} has no live version; pass a version")
        result = run_agent(
            original.agent, original.question, llm=llm_factory() if llm_factory else None,
            tenant=original.tenant, spec=registry.spec_of(row), replay_of=run_id,
            replay_mode=mode,
        )

    new_run = runs.record_run(s, result, redact=redact)
    audit.log(
        s, action="replay", decision="allow", agent=original.agent, version=new_run.version,
        tenant=original.tenant, run_id=new_run.run_id,
        detail={"of": run_id, "mode": mode, "actor": actor},
    )
    return new_run, compare(original, new_run)


def replays_of(s: Session, run_id: str) -> list[Run]:
    return list(
        s.scalars(select(Run).where(Run.replay_of == run_id).order_by(Run.started_at.desc()))
    )
