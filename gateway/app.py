"""Agent gateway: serves whichever version the registry says is live, and records every run."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from aoc_runtime.config import get_settings
from aoc_runtime.runner import run_agent
from console import registry, runs
from console.api.app import run_out
from console.db import default_session_factory


class RunRequest(BaseModel):
    question: str
    tenant: str | None = None


def create_gateway(
    session_factory: sessionmaker[Session] | None = None,
    llm_factory: Callable[[], object] | None = None,
) -> FastAPI:
    factory = session_factory or default_session_factory()
    app = FastAPI(title="AgentOps Gateway")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.post("/v1/agents/{name}/runs")
    def create_run(name: str, body: RunRequest) -> dict:
        with factory() as s:
            live = registry.served_version(s, name, "prod")
            if live is None and get_settings().aoc_env != "prod":
                live = registry.served_version(s, name, "staging")  # non-prod may serve staging
            if live is None:
                raise HTTPException(409, f"no live version of {name}; promote one first")
            spec = registry.spec_of(live)
            llm = llm_factory() if llm_factory else None
            result = run_agent(name, body.question, llm=llm, tenant=body.tenant, spec=spec)
            run = runs.record_run(s, result)
            return run_out(run, detail=True)

    return app
