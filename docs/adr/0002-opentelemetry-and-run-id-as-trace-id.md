# 2. OpenTelemetry with OpenInference, and run_id equal to trace_id

**Status:** accepted

## Context
We need traces that correlate prompts, tool calls, retrieval and responses, metrics for dashboards
and alerts, and a way to tie a stored run to its telemetry. The telemetry backend should be
replaceable.

## Decision
- Instrument with OpenTelemetry. Use OpenInference for LLM, chain and tool span semantics and our own
  `aoc.*` attributes for operations concepts (see `docs/SEMCONV.md`).
- A span processor stamps the run context (agent, version, tenant, run id, environment) on every span,
  including auto-instrumented ones, so nothing relies on developers remembering to tag.
- The run id **is** the trace id.
- Metrics go through OpenTelemetry to Prometheus. `run_id` is never a metric label.

## Consequences
- One identifier links the run record, trace, audit entry, incident and replay.
- The backend is a Collector exporter setting: Phoenix today, Azure Monitor or Langfuse later with no
  code change.
- A test fails if any span in a run is missing a required attribute.
- Trade-off: when tracing is not configured the span context is invalid (trace id 0). We found this
  bug in practice (replays collided) and now fall back to a random id.
