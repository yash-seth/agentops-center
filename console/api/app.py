"""Ops Console API: registry, deployments, runs. The Streamlit UI is a client of this API."""

from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from aoc_runtime import guardrails
from aoc_runtime.config import get_settings

from .. import audit, incidents, registry, replay, runs
from ..db import AgentVersion, AuditEvent, Deployment, Incident, Run, default_session_factory


class IncidentBody(BaseModel):
    title: str
    severity: str = "warning"
    agent: str = ""
    version: str = ""
    tool: str = ""
    actor: str = "unknown"
    note: str = ""


class ReplayBody(BaseModel):
    mode: str = "deterministic"
    version: str | None = None
    actor: str = "unknown"


class ActorBody(BaseModel):
    actor: str = "unknown"


class ResolveBody(BaseModel):
    actor: str = "unknown"
    root_cause: str


class NoteBody(BaseModel):
    actor: str = "unknown"
    message: str


def incident_out(i: Incident, detail: bool = False) -> dict:
    out = {
        "id": i.id, "title": i.title, "severity": i.severity, "status": i.status,
        "source": i.source, "alert_name": i.alert_name, "agent": i.agent, "version": i.version,
        "tenant": i.tenant, "tool": i.tool, "runbook": i.runbook, "root_cause": i.root_cause,
        "firings": i.firings, "created_at": i.created_at.isoformat(),
        "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
        "run_count": len(i.runs),
    }
    if detail:
        out["runs"] = [{"run_id": r.run_id, "reason": r.reason} for r in i.runs]
        out["events"] = [
            {"ts": e.ts.isoformat(), "kind": e.kind, "actor": e.actor, "message": e.message}
            for e in i.events
        ]
    return out


def audit_out(e: AuditEvent) -> dict:
    return {
        "id": e.id, "ts": e.ts.isoformat(), "action": e.action, "decision": e.decision,
        "agent": e.agent, "version": e.version, "tenant": e.tenant, "run_id": e.run_id,
        "input_hash": e.input_hash[:12], "output_hash": e.output_hash[:12], "detail": e.detail,
        "hash": e.hash[:12],
    }


class PromoteBody(BaseModel):
    environment: str
    actor: str = "unknown"
    note: str = ""
    image_digest: str | None = None


class RollbackBody(BaseModel):
    actor: str = "unknown"
    to_version: str | None = None
    note: str = ""


def version_out(v: AgentVersion) -> dict:
    return {
        "agent": v.agent_name,
        "version": v.version,
        "status": v.status,
        "prompt_version": v.prompt_version,
        "content_hash": v.content_hash[:12],
        "model_allowlist": v.spec.get("model_allowlist", []),
        "tools": v.spec.get("tools", []),
        "limits": v.spec.get("limits", {}),
        "eval_scores": v.eval_scores,
        "created_at": v.created_at.isoformat(),
    }


def deployment_out(d: Deployment) -> dict:
    return {
        "id": d.id, "agent": d.agent_name, "version": d.version, "environment": d.environment,
        "action": d.action, "actor": d.actor, "note": d.note, "image_digest": d.image_digest,
        "created_at": d.created_at.isoformat(),
    }


def run_out(r: Run, detail: bool = False) -> dict:
    out = {
        "run_id": r.run_id, "agent": r.agent, "version": r.version, "tenant": r.tenant,
        "app_id": r.app_id, "environment": r.environment, "status": r.status,
        "loop_reason": r.loop_reason, "model": r.model, "steps": r.steps,
        "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
        "cost_usd": r.cost_usd, "latency_s": r.latency_s, "started_at": r.started_at.isoformat(),
        "replay_of": r.replay_of, "replay_mode": r.replay_mode,
        "trace_url": get_settings().trace_url(r.run_id),
    }
    if detail:
        out |= {
            "question": r.question,
            "answer": r.answer,
            "step_records": [
                {
                    "idx": st.idx, "kind": st.kind, "name": st.name, "input": st.input,
                    "output": st.output, "status": st.status, "latency_ms": st.latency_ms,
                    "input_tokens": st.input_tokens, "output_tokens": st.output_tokens,
                }
                for st in r.step_records
            ],
        }
    return out


def create_app(
    session_factory: sessionmaker[Session] | None = None,
    llm_factory: Callable[[], object] | None = None,
) -> FastAPI:
    factory = session_factory or default_session_factory()
    app = FastAPI(title="AgentOps Console API")

    def get_session() -> Iterator[Session]:
        with factory() as s:
            yield s

    db = Depends(get_session)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.post("/registry/sync")
    def sync(s: Session = db) -> dict:
        try:
            rows = registry.sync_from_repo(s)
        except registry.RegistryError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"registered": [f"{r.agent_name}@{r.version}" for r in rows]}

    @app.get("/agents")
    def agents(s: Session = db) -> list[dict]:
        out = []
        for name in sorted({v.agent_name for v in s.query(AgentVersion).all()}):
            prod = registry.served_version(s, name, "prod")
            staging = registry.served_version(s, name, "staging")
            out.append(
                {
                    "name": name,
                    "versions": len(registry.list_versions(s, name)),
                    "prod": prod.version if prod else None,
                    "staging": staging.version if staging else None,
                }
            )
        return out

    @app.get("/agents/{name}/versions")
    def versions(name: str, s: Session = db) -> list[dict]:
        return [version_out(v) for v in registry.list_versions(s, name)]

    @app.get("/agents/{name}/deployments")
    def agent_deployments(name: str, s: Session = db) -> list[dict]:
        return [deployment_out(d) for d in registry.deployments(s, name)]

    @app.post("/agents/{name}/versions/{version}/promote")
    def promote(name: str, version: str, body: PromoteBody, s: Session = db) -> dict:
        try:
            dep = registry.promote(
                s, name, version, body.environment, body.actor, body.note, body.image_digest
            )
        except registry.RegistryError as exc:
            raise HTTPException(409, str(exc)) from exc
        return deployment_out(dep)

    @app.post("/agents/{name}/rollback")
    def rollback(name: str, body: RollbackBody, s: Session = db) -> dict:
        try:
            dep = registry.rollback(s, name, body.actor, body.to_version, body.note)
        except registry.RegistryError as exc:
            raise HTTPException(409, str(exc)) from exc
        return deployment_out(dep)

    @app.get("/runs")
    def list_runs(
        agent: str | None = None,
        version: str | None = None,
        tenant: str | None = None,
        status: str | None = None,
        limit: int = Query(50, le=500),
        s: Session = db,
    ) -> list[dict]:
        rows = runs.list_runs(
            s, agent=agent, version=version, tenant=tenant, status=status, limit=limit
        )
        return [run_out(r) for r in rows]

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, s: Session = db) -> dict:
        run = runs.get_run(s, run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        return run_out(run, detail=True)

    @app.post("/runs/{run_id}/replay")
    def replay_run(run_id: str, body: ReplayBody, s: Session = db) -> dict:
        try:
            new_run, diff = replay.replay_run(
                s, run_id, mode=body.mode, actor=body.actor, version=body.version,
                llm_factory=llm_factory, redact=guardrails.redact_text,
            )
        except replay.ReplayError as exc:
            code = 404 if "not found" in str(exc) else 409
            raise HTTPException(code, str(exc)) from exc
        except registry.RegistryError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"run": run_out(new_run, detail=True), "diff": diff}

    @app.get("/runs/{run_id}/replays")
    def list_replays(run_id: str, s: Session = db) -> list[dict]:
        return [run_out(r) for r in replay.replays_of(s, run_id)]

    @app.get("/stats/versions")
    def stats(s: Session = db) -> list[dict]:
        return runs.summary_by_version(s)

    def incident_call(fn, *args, **kwargs) -> dict:
        try:
            return incident_out(fn(*args, **kwargs), detail=True)
        except incidents.IncidentError as exc:
            code = 404 if "not found" in str(exc) else 409
            raise HTTPException(code, str(exc)) from exc

    @app.post("/alerts")
    def alerts_webhook(payload: dict, s: Session = db) -> dict:
        """Alertmanager webhook: firing alerts open (or update) incidents."""
        touched = incidents.handle_alerts(s, payload)
        return {"incidents": [i.id for i in touched]}

    @app.get("/incidents")
    def list_incidents(status: str | None = None, s: Session = db) -> list[dict]:
        return [incident_out(i) for i in incidents.list_incidents(s, status)]

    @app.post("/incidents")
    def open_incident(body: IncidentBody, s: Session = db) -> dict:
        return incident_call(
            incidents.open_manual, s, title=body.title, severity=body.severity, agent=body.agent,
            version=body.version, tool=body.tool, actor=body.actor, note=body.note,
        )

    @app.get("/incidents/{incident_id}")
    def get_incident(incident_id: int, s: Session = db) -> dict:
        return incident_call(incidents.get, s, incident_id)

    @app.post("/incidents/{incident_id}/acknowledge")
    def ack_incident(incident_id: int, body: ActorBody, s: Session = db) -> dict:
        return incident_call(incidents.acknowledge, s, incident_id, body.actor)

    @app.post("/incidents/{incident_id}/resolve")
    def resolve_incident(incident_id: int, body: ResolveBody, s: Session = db) -> dict:
        return incident_call(incidents.resolve, s, incident_id, body.actor, body.root_cause)

    @app.post("/incidents/{incident_id}/notes")
    def note_incident(incident_id: int, body: NoteBody, s: Session = db) -> dict:
        return incident_call(incidents.add_note, s, incident_id, body.actor, body.message)

    @app.get("/audit")
    def audit_events(action: str | None = None, limit: int = 100, s: Session = db) -> list[dict]:
        return [audit_out(e) for e in audit.list_events(s, limit, action)]

    @app.get("/audit/verify")
    def audit_verify(s: Session = db) -> dict:
        ok, bad = audit.verify_chain(s)
        return {"ok": ok, "first_bad_event": bad}

    return app
