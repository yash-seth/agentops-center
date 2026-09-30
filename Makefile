# Common tasks. Needs GNU make (Linux, macOS, WSL, or Git Bash with make installed).
# Every target is also a plain command you can copy out; nothing here is magic.

UV ?= uv --python 3.12
OFFLINE = AOC_EMBEDDER=hash AOC_LLM_PROVIDERS=fake AOC_TELEMETRY_ENABLED=false
KIND_CLUSTER ?= agentops
IMAGE_TAG ?= dev
COMPOSE = docker compose -f deploy/compose.yaml

.PHONY: help install lint test eval validate generated check up down logs smoke \
        docker-build kind-up kind-deploy kind-smoke kind-down helm-lint clean-db

help:  ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

install:  ## install dependencies
	$(UV) sync --frozen --extra pgvector --extra ui

lint:  ## ruff
	$(UV) run ruff check .

test:  ## unit tests
	$(UV) run pytest

eval:  ## deterministic evals against the baseline (fails on regression)
	$(UV) run aoc eval --check --out evals/out/scores.json --markdown evals/out/report.md

validate:  ## agent onboarding checks
	$(UV) run aoc validate

generated:  ## regenerate committed generated files (dashboard, Helm config copies)
	$(UV) run python scripts/build_dashboard.py
	$(UV) run python scripts/sync_helm_files.py

check: lint validate test eval  ## everything CI runs before merging

up:  ## full stack in docker compose (Postgres, Phoenix, Grafana, gateway, console ...)
	$(COMPOSE) up --build -d

down:  ## stop the compose stack
	$(COMPOSE) down

logs:  ## follow compose logs
	$(COMPOSE) logs -f --tail=100

smoke:  ## end-to-end smoke test against localhost (compose or kind)
	$(UV) run python scripts/smoke_test.py --console http://localhost:8001 --gateway http://localhost:8000

docker-build:  ## build the three images locally
	for t in gateway console-api console-ui; do \
	  docker build -f deploy/docker/Dockerfile --target $$t -t agentops-$$t:$(IMAGE_TAG) . || exit 1; \
	done

helm-lint:  ## lint the chart (needs helm)
	helm lint deploy/helm/agentops
	helm lint deploy/helm/agentops --set observability.enabled=false

kind-up:  ## create the local cluster with host port mappings
	kind create cluster --config deploy/kind/cluster.yaml

kind-deploy: docker-build  ## build images, load them into kind and install the chart
	for t in gateway console-api console-ui; do \
	  kind load docker-image agentops-$$t:$(IMAGE_TAG) --name $(KIND_CLUSTER) || exit 1; \
	done
	helm upgrade --install agentops deploy/helm/agentops --set image.tag=$(IMAGE_TAG) --wait --timeout 10m

kind-smoke: smoke  ## alias: the same smoke test works against the cluster

kind-down:  ## delete the local cluster
	kind delete cluster --name $(KIND_CLUSTER)

clean-db:  ## remove the local SQLite dev database (there are no migrations yet)
	rm -f data/console.db
