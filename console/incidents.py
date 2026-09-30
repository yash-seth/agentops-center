"""Incident management: alerts open incidents, exemplar failing runs are attached automatically.

An "affected" run is one that failed outright, was stopped by the loop guard, or survived only
because the agent worked around a failing tool step (retries can hide failures from run status).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from .db import Incident, IncidentEvent, IncidentRun, Run, RunStep

SEVERITIES = {"critical", "warning", "info"}
STATUSES = ("open", "acknowledged", "resolved")
EXEMPLAR_WINDOW = timedelta(minutes=30)
MAX_EXEMPLARS = 10


class IncidentError(Exception):
    pass


def _event(s: Session, inc: Incident, kind: str, message: str = "", actor: str = "system") -> None:
    inc.events.append(IncidentEvent(kind=kind, actor=actor, message=message))


def _fingerprint(alert: dict) -> str:
    if alert.get("fingerprint"):
        return str(alert["fingerprint"])
    labels = json.dumps(alert.get("labels", {}), sort_keys=True)
    return hashlib.sha1(labels.encode()).hexdigest()[:16]


def find_exemplars(
    s: Session, *, agent: str, version: str = "", tool: str = "", tenant: str = "",
    reference: datetime | None = None,
) -> list[tuple[Run, str]]:
    """Recent affected runs matching the alert's labels, newest first, with why each matched."""
    reference = reference or datetime.now(UTC)
    since = reference - EXEMPLAR_WINDOW
    failed_step = exists().where(
        RunStep.run_id == Run.run_id,
        RunStep.status != "ok",
        *([RunStep.name == tool] if tool else []),
    )
    until = reference + timedelta(minutes=5)
    q = select(Run).where(Run.started_at >= since, Run.started_at <= until)
    if agent:
        q = q.where(Run.agent == agent)
    if version:
        q = q.where(Run.version == version)
    if tenant:
        q = q.where(Run.tenant == tenant)
    q = q.where(or_(Run.status != "ok", failed_step) if not tool else failed_step)
    rows = s.scalars(q.order_by(Run.started_at.desc()).limit(MAX_EXEMPLARS))
    out = []
    for run in rows:
        if run.loop_reason:
            why = f"loop: {run.loop_reason}"
        elif run.status != "ok":
            why = f"run {run.status}"
        else:
            why = f"failed tool step{f' ({tool})' if tool else ''}"
        out.append((run, why))
    return out


def _attach(s: Session, inc: Incident, reference: datetime | None = None) -> int:
    have = {r.run_id for r in inc.runs}
    added = 0
    for run, why in find_exemplars(
        s, agent=inc.agent, version=inc.version, tool=inc.tool, tenant=inc.tenant,
        reference=reference,
    ):
        if run.run_id not in have:
            inc.runs.append(IncidentRun(run_id=run.run_id, reason=why))
            added += 1
    if added:
        _event(s, inc, "runs_attached", f"attached {added} exemplar run(s)")
    return added


def handle_alerts(s: Session, payload: dict) -> list[Incident]:
    """Process an Alertmanager webhook payload. Returns the incidents touched."""
    touched: list[Incident] = []
    for alert in payload.get("alerts", []):
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        fp = _fingerprint(alert)
        existing = s.scalar(
            select(Incident).where(Incident.fingerprint == fp, Incident.status != "resolved")
        )
        if alert.get("status") == "resolved":
            if existing is not None:
                _event(s, existing, "alert_resolved", "alert stopped firing; confirm and resolve")
                touched.append(existing)
            continue
        if existing is not None:
            existing.firings += 1
            _attach(s, existing)
            touched.append(existing)
            continue
        severity = labels.get("severity", "warning")
        inc = Incident(
            title=annotations.get("summary") or labels.get("alertname", "alert"),
            severity=severity if severity in SEVERITIES else "warning",
            source="alert",
            alert_name=labels.get("alertname", ""),
            fingerprint=fp,
            agent=labels.get("agent", ""),
            version=labels.get("agent_version", ""),
            tenant=labels.get("tenant", ""),
            tool=labels.get("tool", ""),
            labels=labels,
            runbook=annotations.get("runbook", ""),
        )
        s.add(inc)
        _event(s, inc, "opened", f"opened by alert {inc.alert_name}")
        _attach(s, inc)
        touched.append(inc)
    s.commit()
    return touched


def open_manual(
    s: Session, *, title: str, severity: str = "warning", agent: str = "", version: str = "",
    tool: str = "", actor: str = "unknown", note: str = "",
) -> Incident:
    if severity not in SEVERITIES:
        raise IncidentError(f"severity must be one of {sorted(SEVERITIES)}")
    inc = Incident(
        title=title, severity=severity, source="manual", agent=agent, version=version, tool=tool
    )
    s.add(inc)
    _event(s, inc, "opened", note or "opened manually", actor)
    if agent:
        _attach(s, inc)
    s.commit()
    return inc


def get(s: Session, incident_id: int) -> Incident:
    inc = s.get(Incident, incident_id)
    if inc is None:
        raise IncidentError(f"incident {incident_id} not found")
    return inc


def list_incidents(s: Session, status: str | None = None, limit: int = 100) -> list[Incident]:
    q = select(Incident).order_by(Incident.id.desc()).limit(limit)
    if status:
        q = q.where(Incident.status == status)
    return list(s.scalars(q))


def acknowledge(s: Session, incident_id: int, actor: str) -> Incident:
    inc = get(s, incident_id)
    if inc.status != "open":
        raise IncidentError(f"only an open incident can be acknowledged (is {inc.status})")
    inc.status = "acknowledged"
    _event(s, inc, "acknowledged", actor=actor)
    s.commit()
    return inc


def resolve(s: Session, incident_id: int, actor: str, root_cause: str) -> Incident:
    inc = get(s, incident_id)
    if inc.status == "resolved":
        raise IncidentError("incident is already resolved")
    if not root_cause.strip():
        raise IncidentError("a root cause is required to resolve an incident")
    inc.status = "resolved"
    inc.root_cause = root_cause
    inc.resolved_at = datetime.now(UTC)
    _event(s, inc, "resolved", root_cause, actor)
    s.commit()
    return inc


def add_note(s: Session, incident_id: int, actor: str, message: str) -> Incident:
    inc = get(s, incident_id)
    _event(s, inc, "note", message, actor)
    s.commit()
    return inc
