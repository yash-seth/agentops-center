"""Append-only audit log with a hash chain, so tampering with history is detectable.

Each event's hash covers its content and the previous event's hash. Only ``log`` writes; there is
no update or delete. ``verify_chain`` recomputes every hash.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import AuditEvent


def _digest(prev_hash: str, payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256((prev_hash + body).encode()).hexdigest()


def _payload(e: AuditEvent) -> dict:
    return {
        "action": e.action, "decision": e.decision, "agent": e.agent, "version": e.version,
        "tenant": e.tenant, "run_id": e.run_id, "input_hash": e.input_hash,
        "output_hash": e.output_hash, "detail": e.detail,
    }


def log(
    s: Session, *, action: str, decision: str, agent: str = "", version: str = "",
    tenant: str = "", run_id: str | None = None, input_hash: str = "", output_hash: str = "",
    detail: dict | None = None,
) -> AuditEvent:
    last = s.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    prev = last.hash if last else ""
    event = AuditEvent(
        action=action, decision=decision, agent=agent, version=version, tenant=tenant,
        run_id=run_id, input_hash=input_hash, output_hash=output_hash, detail=detail or {},
        prev_hash=prev, hash="",
    )
    event.hash = _digest(prev, _payload(event))
    s.add(event)
    s.commit()
    return event


def verify_chain(s: Session) -> tuple[bool, int | None]:
    """Return (ok, first_bad_event_id)."""
    prev = ""
    for e in s.scalars(select(AuditEvent).order_by(AuditEvent.id)):
        if e.prev_hash != prev or e.hash != _digest(prev, _payload(e)):
            return False, e.id
        prev = e.hash
    return True, None


def list_events(s: Session, limit: int = 100, action: str | None = None) -> list[AuditEvent]:
    q = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if action:
        q = q.where(AuditEvent.action == action)
    return list(s.scalars(q))
