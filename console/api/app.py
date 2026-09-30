"""Ops Console API: registry, deployments, runs. The Streamlit UI is a client of this API."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from aoc_runtime.config import get_settings

from .. import registry, runs
from ..db import AgentVersion, Deployment, Run, default_session_factory


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


def create_app(session_factory: sessionmaker[Session] | None = None) -> FastAPI:
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

    @app.get("/stats/versions")
    def stats(s: Session = db) -> list[dict]:
        return runs.summary_by_version(s)

    return app
