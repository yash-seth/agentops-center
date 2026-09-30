# 3. Phoenix for trace viewing

**Status:** accepted

## Context
We need a trace UI that runs free on a laptop inside a local Kubernetes cluster next to everything
else. Options: Jaeger, Langfuse, Arize Phoenix.

## Decision
Use Arize Phoenix, a single container that accepts OTLP and understands LLM spans.

## Consequences
- Small footprint. Self-hosted Langfuse needs several supporting services (a column store, cache and
  object storage), which is heavy for the 4-8 GB machines this project targets.
- Because the Collector owns the export, switching to Langfuse or a cloud backend is configuration.
- Trade-off: Phoenix's run-to-trace deep-link URL includes a project id that we default but could not
  verify without running it; it is configurable (`AOC_TRACE_URL_TEMPLATE`).
