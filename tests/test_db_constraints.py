import pytest
from sqlalchemy import String

from aoc_runtime.runner import StepRecord
from console import registry
from console.db import (
    Base,
    Deployment,
    Incident,
    Run,
    RunStep,
    memory_session_factory,
    utcnow,
)

# Every status-like value the code can produce. Postgres rejects values longer than the column.
STEP_STATUSES = ["ok", "error", "circuit_open"]
RUN_STATUSES = ["ok", "error", "loop_stopped"]
LOOP_REASONS = ["repeat", "oscillation", "step_budget", "cost_budget"]


def make_run(status="ok", loop_reason=None):
    return Run(
        run_id="a" * 32, agent="a", version="1.0.0", tenant="t", app_id="x", environment="dev",
        status=status, loop_reason=loop_reason, question="q", answer="a", started_at=utcnow(),
    )


def test_every_known_step_status_fits_its_column():
    with memory_session_factory()() as s:
        s.add(make_run())
        for i, status in enumerate(STEP_STATUSES):
            s.add(RunStep(run_id="a" * 32, idx=i, kind="tool", name="t", status=status))
        s.commit()  # raises if any value exceeds the column (the circuit_open bug)


@pytest.mark.parametrize("status", RUN_STATUSES)
@pytest.mark.parametrize("reason", LOOP_REASONS)
def test_run_status_and_loop_reason_fit(status, reason):
    with memory_session_factory()() as s:
        s.add(make_run(status, reason))
        s.commit()


def test_oversize_values_are_rejected_on_every_backend():
    """SQLite would silently accept these; the guard makes it behave like Postgres."""
    with memory_session_factory()() as s:
        s.add(make_run(status="x" * 21))
        with pytest.raises(ValueError, match="Run.status is 21 characters but .* allows 20"):
            s.commit()


def test_guard_also_covers_updates():
    with memory_session_factory()() as s:
        s.add(make_run())
        s.commit()
        run = s.get(Run, "a" * 32)
        run.status = "y" * 25
        with pytest.raises(ValueError, match="column allows 20"):
            s.commit()


def test_text_columns_stay_unbounded():
    with memory_session_factory()() as s:
        run = make_run()
        run.question, run.answer = "q" * 100_000, "a" * 100_000
        s.add(run)
        s.commit()


def test_the_guard_would_have_caught_the_original_circuit_open_bug(monkeypatch):
    """Regression: RunStep.status was String(10) and "circuit_open" has 12 characters. Postgres
    rejected it (HTTP 500 whenever a breaker opened) while SQLite-based tests passed."""
    monkeypatch.setattr(RunStep.__table__.c.status.type, "length", 10)
    with memory_session_factory()() as s:
        s.add(make_run())
        s.add(RunStep(run_id="a" * 32, idx=0, kind="tool", name="t", status="circuit_open"))
        with pytest.raises(ValueError, match="RunStep.status is 12 characters .* allows 10"):
            s.commit()


def test_registry_and_incident_enums_fit_their_columns():
    lengths = {
        (m.class_.__name__, c.key): c.type.length
        for m in Base.registry.mappers for c in m.columns
        if isinstance(c.type, String) and c.type.length
    }
    for status in ("draft", "staging", "prod", "retired"):
        assert len(status) <= lengths[("AgentVersion", "status")]
    for status in ("open", "acknowledged", "resolved"):
        assert len(status) <= lengths[("Incident", "status")]
    for action in ("promote", "rollback"):
        assert len(action) <= lengths[("Deployment", "action")]
    for kind in ("llm", "tool"):
        assert len(kind) <= lengths[("RunStep", "kind")]
    for decision in ("allow", "block", "redact"):
        assert len(decision) <= lengths[("AuditEvent", "decision")]
    for action in ("input_check", "redaction", "run", "replay", "chaos"):
        assert len(action) <= lengths[("AuditEvent", "action")]
    _ = (Deployment, Incident, registry, StepRecord)  # imported to ensure all models are mapped
