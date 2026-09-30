"""Run history: persist runs with their steps, and query them for the console."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from aoc_runtime.runner import RunResult

from .db import Run, RunStep

# Replaced by the Presidio-based redactor in week 4; stored text must never bypass this hook.
Redactor = Callable[[str], str]


def _identity(text: str) -> str:
    return text


def record_run(s: Session, result: RunResult, redact: Redactor = _identity) -> Run:
    run = Run(
        run_id=result.run_id,
        agent=result.agent,
        version=result.version,
        tenant=result.tenant,
        app_id=result.app_id,
        environment=result.environment,
        status=result.status,
        loop_reason=result.loop_reason,
        question=redact(result.question),
        answer=redact(result.answer),
        model=result.model,
        steps=result.steps,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
        latency_s=result.latency_s,
        started_at=result.started_at,
        step_records=[
            RunStep(
                idx=st.idx, kind=st.kind, name=st.name, input=st.input, output=redact(st.output),
                status=st.status, latency_ms=st.latency_ms, input_tokens=st.input_tokens,
                output_tokens=st.output_tokens,
            )
            for st in result.step_records
        ],
    )
    s.add(run)
    s.commit()
    return run


def list_runs(
    s: Session,
    *,
    agent: str | None = None,
    version: str | None = None,
    tenant: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[Run]:
    q = select(Run).order_by(Run.started_at.desc()).limit(limit)
    for column, value in (
        (Run.agent, agent), (Run.version, version), (Run.tenant, tenant), (Run.status, status)
    ):
        if value:
            q = q.where(column == value)
    return list(s.scalars(q))


def get_run(s: Session, run_id: str) -> Run | None:
    return s.get(Run, run_id)


def summary_by_version(s: Session) -> list[dict]:
    """Per agent version: volume, success rate, latency, cost, loops (from run history)."""
    rows = s.execute(
        select(
            Run.agent,
            Run.version,
            func.count(Run.run_id),
            func.sum(case((Run.status == "ok", 1), else_=0)),
            func.avg(Run.latency_s),
            func.avg(Run.cost_usd),
            func.sum(case((Run.loop_reason.is_not(None), 1), else_=0)),
        )
        .group_by(Run.agent, Run.version)
        .order_by(Run.agent, Run.version)
    ).all()
    return [
        {
            "agent": agent,
            "version": version,
            "runs": total,
            "success_rate": (ok or 0) / total,
            "avg_latency_s": avg_latency,
            "avg_cost_usd": avg_cost,
            "loops": loops or 0,
        }
        for agent, version, total, ok, avg_latency, avg_cost, loops in rows
    ]
