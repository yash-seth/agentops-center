# 8. Push-based CI verification now, pull-based GitOps in production

**Status:** accepted

## Context
Continuous delivery needs a target. CI runners cannot reach a developer's laptop cluster, and this
project should cost nothing to run.

## Decision
- CI deploys the chart to an ephemeral kind cluster on the runner and runs the same smoke test used
  locally, so every change is proven to install and work.
- Releases publish images to GHCR and attach a registry snapshot (agent specs plus eval scores).
- A manual, OIDC-based workflow deploys a released tag to an optional AKS cluster.

## Consequences
- Cheap, repeatable verification of the chart and images on every change.
- For a real environment the preferred design is pull-based: a controller such as Argo CD or Flux
  reconciles the cluster to a Git commit, with secrets from a managed vault. The Helm chart is
  already parameterised for that (`existingSecret`, image registry and tag).
