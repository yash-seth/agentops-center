# Running and deploying

## Three ways to run it
| Mode | Command | Needs | Includes |
|---|---|---|---|
| Local processes | see README quickstart | Python 3.12, uv | gateway, console API, UI, SQLite |
| docker compose | `make up` | Docker | + Postgres/pgvector, Phoenix, OTel Collector, Prometheus, Alertmanager, Grafana |
| Kubernetes (kind) | `make kind-up kind-deploy` | Docker, kind, helm | the same stack via the Helm chart |

All three use the same smoke test: `make smoke` (`scripts/smoke_test.py`). It syncs the registry,
promotes an agent, makes a governed run (with a PII string that must be redacted), checks the
recorded steps, replays the run, checks the injection guardrail blocks, and verifies the audit chain.

## docker compose
`docker compose -f deploy/compose.yaml up --build`. Offline by default (fake model, hash embeddings).
For real models export `GOOGLE_API_KEY` and set `AOC_LLM_PROVIDERS=gemini,fake AOC_EMBEDDER=fastembed`.
Ports: gateway 8000, console API 8001, UI 8501, Phoenix 6006, Prometheus 9090, Alertmanager 9093,
Grafana 3000.

## Kubernetes with kind
```bash
kind create cluster --config deploy/kind/cluster.yaml     # maps NodePorts to localhost
make kind-deploy                                          # build, load images, helm install
make smoke
```
- The chart is `deploy/helm/agentops`. Defaults suit a laptop (small requests, NodePorts).
- `--set observability.enabled=false` skips Phoenix/Prometheus/Grafana for a lighter install.
- Credentials: `secrets.postgresPassword` is a local default. For anything shared use
  `--set secrets.existingSecret=<name>` (keys: `postgres-password`, `google-api-key`,
  `groq-api-key`).
- Monitoring config (collector, Prometheus, alerts, Alertmanager, Grafana) is copied into the chart
  by `scripts/sync_helm_files.py` so compose and Kubernetes run identical configuration. A test and
  a CI step fail if the copies drift.

## CI/CD (GitHub Actions)
| Workflow | When | Does |
|---|---|---|
| `ci.yml` | every push and PR | ruff, `aoc validate`, tests, deterministic eval gate, generated files up to date; builds the three images; `helm lint` + kubeconform; deploys to a kind cluster and runs the smoke test |
| `evals.yml` | PR labelled `run-evals`, nightly, manual | full evals (real model if `GOOGLE_API_KEY` is set), posts the score table on the PR, fails on regression |
| `release.yml` | tag `v*` | re-runs the gates, publishes the images to GHCR, attaches a registry snapshot (agent specs plus eval scores) to the GitHub release |

Deployment is push-based from CI to an ephemeral kind cluster for verification. In production I would
use a pull-based GitOps controller (Argo CD or Flux) so the cluster reconciles itself to a Git commit.

## Cost
Everything runs on free tooling: open-source software, free model tiers, local Kubernetes, and
GitHub Actions (free for public repositories).
