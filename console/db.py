"""Console database: agent registry, deployments, run history and recorded steps.

SQLite by default (zero setup); set AOC_DB_URL to a Postgres URL (postgresql+psycopg://...) for
the compose/Kubernetes deployment. Tables are created with ``create_all`` for now.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)
from sqlalchemy.pool import StaticPool

from aoc_runtime.config import DATA_DIR, get_settings


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Agent(Base):
    __tablename__ = "agents"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    owner: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    versions: Mapped[list[AgentVersion]] = relationship(back_populates="agent")


class AgentVersion(Base):
    """An immutable, versioned snapshot of an agent spec plus its prompt text."""

    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_name", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_name: Mapped[str] = mapped_column(ForeignKey("agents.name"), index=True)
    version: Mapped[str] = mapped_column(String(30))
    prompt_version: Mapped[str] = mapped_column(String(30))
    content_hash: Mapped[str] = mapped_column(String(64))
    spec: Mapped[dict] = mapped_column(JSON)
    eval_scores: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|staging|prod|retired
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    agent: Mapped[Agent] = relationship(back_populates="versions")


class Deployment(Base):
    """Append-only history of promotions and rollbacks."""

    __tablename__ = "deployments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(30))
    environment: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(20))  # promote | rollback
    actor: Mapped[str] = mapped_column(String(100))
    note: Mapped[str] = mapped_column(Text, default="")
    image_digest: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Run(Base):
    """One agent invocation. ``run_id`` is the OpenTelemetry trace id."""

    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(30), index=True)
    tenant: Mapped[str] = mapped_column(String(100), index=True)
    app_id: Mapped[str] = mapped_column(String(100))
    environment: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), index=True)
    loop_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(100), default="")
    steps: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_s: Mapped[float] = mapped_column(Float, default=0.0)
    replay_of: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    replay_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    step_records: Mapped[list[RunStep]] = relationship(
        back_populates="run", order_by="RunStep.idx", cascade="all, delete-orphan"
    )


class RunStep(Base):
    __tablename__ = "run_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10))  # llm | tool
    name: Mapped[str] = mapped_column(String(100))
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="ok")
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    run: Mapped[Run] = relationship(back_populates="step_records")


def make_engine(url: str) -> Engine:
    if url in ("sqlite://", "sqlite:///:memory:"):
        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


@lru_cache
def default_session_factory() -> sessionmaker[Session]:
    url = get_settings().aoc_db_url or f"sqlite:///{(DATA_DIR / 'console.db').as_posix()}"
    engine = make_engine(url)
    # Several processes (console API, gateway) may create the tables at the same moment on a fresh
    # database, and the database may still be starting; retry instead of crashing.
    for attempt in range(10):
        try:
            Base.metadata.create_all(engine)
            break
        except Exception:  # noqa: BLE001
            if attempt == 9:
                raise
            time.sleep(2)
    return sessionmaker(engine, expire_on_commit=False)


def memory_session_factory() -> sessionmaker[Session]:
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


class AuditEvent(Base):
    """Append-only, hash-chained audit trail. There is deliberately no update or delete API."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    action: Mapped[str] = mapped_column(String(30), index=True)  # input_check|redaction|run
    decision: Mapped[str] = mapped_column(String(20))  # allow|block|redact
    agent: Mapped[str] = mapped_column(String(100), default="")
    version: Mapped[str] = mapped_column(String(30), default="")
    tenant: Mapped[str] = mapped_column(String(100), default="")
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    input_hash: Mapped[str] = mapped_column(String(64), default="")
    output_hash: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    hash: Mapped[str] = mapped_column(String(64))


class Incident(Base):
    """Opened by an alert (or by hand); carries exemplar runs and a triage timeline."""

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(20), default="warning")  # critical|warning|info
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    source: Mapped[str] = mapped_column(String(20), default="alert")  # alert|manual
    alert_name: Mapped[str] = mapped_column(String(100), default="")
    fingerprint: Mapped[str] = mapped_column(String(64), default="", index=True)
    agent: Mapped[str] = mapped_column(String(100), default="", index=True)
    version: Mapped[str] = mapped_column(String(30), default="")
    tenant: Mapped[str] = mapped_column(String(100), default="")
    tool: Mapped[str] = mapped_column(String(100), default="")
    labels: Mapped[dict] = mapped_column(JSON, default=dict)
    runbook: Mapped[str] = mapped_column(String(300), default="")
    root_cause: Mapped[str] = mapped_column(Text, default="")
    firings: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    events: Mapped[list[IncidentEvent]] = relationship(
        back_populates="incident", order_by="IncidentEvent.id", cascade="all, delete-orphan"
    )
    runs: Mapped[list[IncidentRun]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )


class IncidentEvent(Base):
    __tablename__ = "incident_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    kind: Mapped[str] = mapped_column(String(30))
    actor: Mapped[str] = mapped_column(String(100), default="system")
    message: Mapped[str] = mapped_column(Text, default="")
    incident: Mapped[Incident] = relationship(back_populates="events")


class IncidentRun(Base):
    """Exemplar run attached to an incident."""

    __tablename__ = "incident_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(100), default="")
    incident: Mapped[Incident] = relationship(back_populates="runs")
