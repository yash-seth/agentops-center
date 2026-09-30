# Telemetry semantic conventions

Single source of truth in code: `aoc_runtime/semconv.py`. Every span created during a run is
stamped with the run context by `RunContextSpanProcessor`, including spans produced by
auto-instrumentation, so nothing has to be tagged by hand. A test fails if any span is missing a
required attribute.

## Naming policy
- Use OpenTelemetry names where a standard exists (`service.name`, `deployment.environment`,
  `gen_ai.*`).
- Use the `aoc.*` namespace for operations-center concepts the standard does not cover.
- `run_id` is the OTel `trace_id` (32 hex chars). It links a run record, its trace and its logs.

## Span attributes
| Attribute | On | Meaning |
|---|---|---|
| `aoc.agent.name` / `aoc.agent.version` | every span | Agent identity from `agent.yaml` |
| `aoc.prompt.version` | every span | Prompt file version used |
| `aoc.tenant.id` / `aoc.app.id` | every span | Who the run is for |
| `aoc.run.id` | every span | Same as the trace id |
| `deployment.environment` | every span | dev / staging / prod |
| `aoc.run.status`, `aoc.run.steps` | `agent.run` | ok, error, loop_stopped; model steps taken |
| `gen_ai.system`, `gen_ai.request.model` | `agent.run` | Provider and model |
| `gen_ai.usage.input_tokens` / `output_tokens` | `agent.run` | Token usage |
| `aoc.cost.usd` | `agent.run` | List-price-equivalent cost of the run |
| `aoc.loop.detected` | `agent.run` | True when the loop guard stopped the run |
| `aoc.tool.name`, `aoc.tool.status` | `tool.*` | Tool name; ok, error or circuit_open |
| `aoc.tool.latency_ms` | `tool.*` | Duration of this attempt |
| `aoc.tool.attempt` | `tool.*` | 1 for the first try, 2+ for retries; each attempt is its own span |
| `aoc.tool.circuit` | `tool.*` | `open` when the circuit breaker refused the call |
| `aoc.replay`, `aoc.replay.of`, `aoc.replay.mode` | every span of a replay | Marks replay traffic and links it to the original run |

Span event `loop_detected` carries the `reason`: repeat, oscillation, step_budget, cost_budget.

## Metrics (Prometheus names)
Labels on all metrics: `agent`, `agent_version`, `tenant`, `env`. `run_id` is never a label: it is
unbounded, so it stays in traces and the run store.

| Metric | Extra labels | Type |
|---|---|---|
| `aoc_runs_total` | `status` | counter |
| `aoc_run_duration_seconds` | | histogram |
| `aoc_tool_calls_total` | `tool`, `status` | counter |
| `aoc_tool_duration_seconds` | `tool` | histogram |
| `aoc_llm_tokens_total` | `model`, `direction` | counter |
| `aoc_run_cost_usd` | | histogram |
| `aoc_cost_usd_total` | | counter |
| `aoc_loops_detected_total` | `reason` | counter |
| `aoc_guardrail_blocks_total` | `type` | counter |

The dashboard (`scripts/build_dashboard.py`) and alert rules (`deploy/prometheus/alerts.yml`) may
only use these names; `tests/test_deploy_assets.py` enforces it.

## Cost
Free-tier calls cost nothing in reality, but showback uses the price you would pay, so cost is
always computed from the price table in `aoc_runtime/cost.py` (verify prices before quoting them).

## Replays
Replay runs are stored as runs of their own (new trace id) linked to the original, tagged with
`aoc.replay=true` on every span, and excluded from production metrics so they never distort
success rate, latency or cost. Deterministic replays use their own circuit-breaker namespace.
