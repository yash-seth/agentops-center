"""Agent gateway: guardrails, then whichever version the registry says is live, recorded per run."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from aoc_runtime import faults, guardrails, metrics
from aoc_runtime.config import get_settings
from aoc_runtime.runner import run_agent
from console import audit, registry, runs
from console.api.app import run_out
from console.db import default_session_factory

NOT_CAPTURED = "[content not captured]"


class FaultBody(BaseModel):
    tool: str = "*"
    error_rate: float = Field(0.0, ge=0.0, le=1.0)
    latency_s: float = Field(0.0, ge=0.0, le=30.0)
    force_loop: bool = False


class ChaosBody(BaseModel):
    faults: list[FaultBody]
    actor: str = "unknown"


class RunRequest(BaseModel):
    question: str
    tenant: str | None = None


def storage_redactor() -> Callable[[str], str]:
    if get_settings().aoc_capture_content:
        return guardrails.redact_text
    return lambda _text: NOT_CAPTURED


def create_gateway(
    session_factory: sessionmaker[Session] | None = None,
    llm_factory: Callable[[], object] | None = None,
) -> FastAPI:
    factory = session_factory or default_session_factory()
    app = FastAPI(title="AgentOps Gateway")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    def require_chaos_api() -> None:
        settings = get_settings()
        if not settings.aoc_enable_chaos_api or settings.aoc_env == "prod":
            raise HTTPException(404, "not found")  # invisible unless explicitly enabled

    @app.get("/admin/chaos")
    def get_chaos() -> dict:
        require_chaos_api()
        return {"faults": [vars(f) for f in faults.get_faults()]}

    @app.put("/admin/chaos")
    def set_chaos(body: ChaosBody) -> dict:
        require_chaos_api()
        faults.set_faults([faults.Fault(**f.model_dump()) for f in body.faults])
        with factory() as s:
            audit.log(
                s, action="chaos", decision="allow",
                detail={"faults": [f.model_dump() for f in body.faults], "actor": body.actor},
            )
        return {"faults": [vars(f) for f in faults.get_faults()]}

    @app.delete("/admin/chaos")
    def clear_chaos(actor: str = "unknown") -> dict:
        require_chaos_api()
        faults.clear_faults()
        with factory() as s:
            audit.log(s, action="chaos", decision="allow", detail={"faults": [], "actor": actor})
        return {"faults": []}

    @app.post("/v1/agents/{name}/runs")
    def create_run(name: str, body: RunRequest) -> dict:
        settings = get_settings()
        with factory() as s:
            live = registry.served_version(s, name, "prod")
            if live is None and settings.aoc_env != "prod":
                live = registry.served_version(s, name, "staging")  # non-prod may serve staging
            if live is None:
                raise HTTPException(409, f"no live version of {name}; promote one first")
            spec = registry.spec_of(live)
            tenant = body.tenant or spec.tenant
            who = {"agent": name, "version": live.version, "tenant": tenant}
            original_hash = guardrails.sha256(body.question)

            rule = guardrails.check_injection(body.question)
            if rule:
                audit.log(
                    s, action="input_check", decision="block", input_hash=original_hash,
                    detail={"rule": rule}, **who,
                )
                metrics.record_guardrail_block(
                    "prompt_injection", agent=name, tenant=tenant, env=settings.aoc_env
                )
                raise HTTPException(422, {"error": "blocked_by_guardrail", "rule": rule})

            redacted = guardrails.redact(body.question)
            if redacted.changed:
                audit.log(
                    s, action="redaction", decision="redact", input_hash=original_hash,
                    detail={"entities": redacted.counts}, **who,
                )

            llm = llm_factory() if llm_factory else None
            result = run_agent(name, redacted.text, llm=llm, tenant=body.tenant, spec=spec)
            run = runs.record_run(s, result, redact=storage_redactor())
            audit.log(
                s, action="run", decision="allow", run_id=result.run_id,
                input_hash=original_hash, output_hash=guardrails.sha256(result.answer),
                detail={"status": result.status, "cost_usd": result.cost_usd}, **who,
            )
            return run_out(run, detail=True) | {"redactions": redacted.counts}

    return app
