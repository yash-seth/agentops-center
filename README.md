# AgentOps Center

An operations console for production AI agents: a versioned agent registry, deployment history,
run history with recorded steps, end-to-end OpenTelemetry tracing, Prometheus metrics with a
Grafana dashboard and alert rules, loop detection, and fault injection for incident drills.

Two demo agents (LangGraph) serve a fictional snack company: a supply-chain assistant (RAG + SQL +
ticket tools) and an HR policy bot (RAG). Everything runs on free tooling.

## Status
| Area | State |
|---|---|
| Agents, RAG (local + pgvector), tracing, metrics, loop guard, chaos | done, unit tested |
| Registry, promote/rollback, run history, console API, gateway, Streamlit UI | done, unit tested |
| Grafana dashboard + alert rules (as code) | done, validated offline; not yet viewed live |
| Traces in Phoenix, pgvector against Postgres | needs Docker (compose file ready) |
| Tool timeouts, retries, circuit breaker | done, unit tested |
| Incidents (alert webhook, exemplar runs, triage timeline), deterministic replay and cross-version rerun | done, unit tested and exercised against live servers |
| FinOps showback, CSV export, model what-if | done, unit tested |
| PII redaction (Presidio), prompt-injection screening, hash-chained audit log | done, unit tested; see docs/RESPONSIBLE_AI.md for limits |
| Evals with a baseline regression gate; `aoc` CLI (scaffold, validate, eval, register, chaos, replay) | done, unit tested |
| Dockerfile, compose stack, Helm chart, kind config, GitHub Actions (CI, evals, release) | written and statically checked; not yet run (needs Docker, kind, helm, GitHub) |
| AKS deployment, demo video, final polish | planned |

## Quickstart (no Docker, no API keys)
```bash
uv sync --python 3.12 --extra ui --extra pgvector
export AOC_EMBEDDER=hash AOC_LLM_PROVIDERS=fake AOC_TELEMETRY_ENABLED=false

uv run --python 3.12 uvicorn console.api.main:app --port 8001 &
uv run --python 3.12 uvicorn gateway.main:app --port 8000 &
curl -X POST localhost:8001/registry/sync

# take a version live: draft -> staging -> prod
curl -X POST localhost:8001/agents/hr-policy-bot/versions/1.0.0/promote \
  -H 'content-type: application/json' -d '{"environment":"staging","actor":"me"}'
curl -X POST localhost:8001/agents/hr-policy-bot/versions/1.0.0/promote \
  -H 'content-type: application/json' -d '{"environment":"prod","actor":"me"}'

# call the agent; the gateway serves whichever version is live and records the run
curl -X POST localhost:8000/v1/agents/hr-policy-bot/runs \
  -H 'content-type: application/json' -d '{"question":"How many sick days do I get?"}'

uv run --python 3.12 streamlit run console/ui/app.py   # console UI on :8501
```
Gateway metrics for Prometheus are at `localhost:8000/metrics`.

To use real models set `GOOGLE_API_KEY` and/or `GROQ_API_KEY` (see `.env.example`); the runtime
falls back down the provider list automatically.

## Full stack (needs Docker)
```bash
docker compose -f deploy/compose.yaml up   # Postgres+pgvector, Phoenix, OTel Collector, Prometheus, Grafana
```
Phoenix `:6006`, Grafana `:3000`, Prometheus `:9090`.

## Chaos drills
`scripts/generate_traffic.py --chaos tool-errors|latency|loop` generates traffic while injecting a
fault, so the alerts and dashboard have something to show. See `docs/RUNBOOK.md`.

## Incident drill (no Docker)
```bash
# gateway with an injected tool outage
AOC_CHAOS='[{"tool":"rag_search","error_rate":1.0}]' uv run --python 3.12 uvicorn gateway.main:app --port 8000
# send a few requests, then fire an Alertmanager-style webhook at the console
curl -X POST localhost:8001/alerts -H 'content-type: application/json' -d '{"alerts":[{"status":"firing",
  "labels":{"alertname":"ToolFailureRateHigh","agent":"hr-policy-bot","tool":"rag_search"},
  "annotations":{"summary":"rag_search failing","runbook":"docs/RUNBOOK.md#tool-failures"}}]}'
```
Then open the Incidents page, inspect an exemplar run and replay it.

## Evals
`aoc eval --check` runs retrieval, trajectory and answer evals per agent and fails if a gated metric
drops below `evals/baseline.fake.json` by more than 0.05. The deterministic mode (hash embeddings and
a scripted model) runs in every PR; it guards against regressions rather than measuring absolute
quality. Real-model evals run on demand or nightly (`.github/workflows/evals.yml`).

Chunking strategies compared on the supply-chain retrieval set (8 judged queries, hash embeddings):

| strategy | hit@1 | hit@3 | MRR |
|---|---|---|---|
| heading (default) | 0.875 | 1.000 | 0.938 |
| fixed 400 chars | 0.875 | 1.000 | 0.938 |
| paragraph | 0.625 | 1.000 | 0.812 |

The set is small and the documents are tiny, so treat these as a demonstration of the method.

## New agent in minutes
`aoc new-agent claims-helper --template rag` scaffolds the agent, sample documents and an eval
dataset, and validates them. See `docs/ONBOARDING.md`.

## Docs
`docs/SEMCONV.md` telemetry conventions, `docs/RUNBOOK.md` alert playbooks and triage workflow,
`docs/RESPONSIBLE_AI.md` guardrails, audit trail and their limits, `docs/ONBOARDING.md` adding an agent,
`docs/DEPLOY.md` compose, Kubernetes and CI/CD.

## Tests
```bash
uv run --python 3.12 pytest && uv run --python 3.12 ruff check .
```
