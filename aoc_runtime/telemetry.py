"""OpenTelemetry setup.

Every span created while a run is active is stamped with the run context (agent, version, tenant,
run id ...) by ``RunContextSpanProcessor``. That includes spans produced by auto-instrumentation
(OpenInference), so nobody has to remember to tag them by hand.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)

from . import semconv as sc
from .config import get_settings


@dataclass(frozen=True)
class RunContext:
    agent_name: str
    agent_version: str
    prompt_version: str
    tenant_id: str
    app_id: str
    run_id: str
    environment: str
    extra: dict[str, str] = field(default_factory=dict)

    def attributes(self) -> dict[str, str]:
        return {
            sc.AGENT_NAME: self.agent_name,
            sc.AGENT_VERSION: self.agent_version,
            sc.PROMPT_VERSION: self.prompt_version,
            sc.TENANT_ID: self.tenant_id,
            sc.APP_ID: self.app_id,
            sc.RUN_ID: self.run_id,
            sc.DEPLOYMENT_ENVIRONMENT: self.environment,
            **self.extra,
        }


_current_run: contextvars.ContextVar[RunContext | None] = contextvars.ContextVar(
    "aoc_run", default=None
)


def current_run() -> RunContext | None:
    return _current_run.get()


@contextmanager
def run_scope(ctx: RunContext) -> Iterator[RunContext]:
    token = _current_run.set(ctx)
    try:
        yield ctx
    finally:
        _current_run.reset(token)


class RunContextSpanProcessor(SpanProcessor):
    """Copies the active RunContext onto every span at start."""

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        ctx = _current_run.get()
        if ctx is not None:
            span.set_attributes(ctx.attributes())

    def on_end(self, span: ReadableSpan) -> None:  # pragma: no cover - nothing to do
        pass


_provider: TracerProvider | None = None


def init_telemetry(
    service_name: str = "aoc-agent",
    exporter: SpanExporter | None = None,
    *,
    instrument_langchain: bool = True,
    simple: bool = False,
) -> TracerProvider:
    """Configure the global tracer provider once. Pass ``exporter`` to override OTLP (tests)."""
    global _provider
    if _provider is not None:
        return _provider

    settings = get_settings()
    resource = Resource.create(
        {sc.SERVICE_NAME: service_name, sc.DEPLOYMENT_ENVIRONMENT: settings.aoc_env}
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(RunContextSpanProcessor())

    if exporter is None and settings.aoc_telemetry_enabled:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces"
        exporter = OTLPSpanExporter(endpoint=endpoint)
    if exporter is not None:
        processor = SimpleSpanProcessor(exporter) if simple else BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)

    trace.set_tracer_provider(provider)
    _provider = provider

    if instrument_langchain:
        from openinference.instrumentation.langchain import LangChainInstrumentor

        LangChainInstrumentor().instrument(tracer_provider=provider)
    return provider


def reset_telemetry_for_tests() -> None:
    global _provider
    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor

        LangChainInstrumentor().uninstrument()
    except Exception:  # noqa: BLE001
        pass
    _provider = None


def get_tracer() -> trace.Tracer:
    # Use our own provider directly: the global one can only be set once per process.
    if _provider is not None:
        return _provider.get_tracer("aoc_runtime")
    return trace.get_tracer("aoc_runtime")
