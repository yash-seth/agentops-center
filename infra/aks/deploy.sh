#!/usr/bin/env bash
# Install or upgrade AgentOps Center on the current kubectl context (an AKS cluster).
#
#   ./infra/aks/deploy.sh <image-tag> [registry-prefix]
#   ./infra/aks/deploy.sh 0.1.0 ghcr.io/<your-github-user>/
#
# Images come from GHCR (published by .github/workflows/release.yml). The packages must be public,
# or create an image pull secret; see infra/aks/README.md.
set -euo pipefail

TAG="${1:?usage: deploy.sh <image-tag> [registry-prefix, e.g. ghcr.io/user/]}"
REGISTRY="${2:-}"
NAMESPACE="${AOC_NAMESPACE:-agentops}"
CHART="$(cd "$(dirname "$0")/../.." && pwd)/deploy/helm/agentops"

[[ -n "$REGISTRY" && "$REGISTRY" != */ ]] && REGISTRY="$REGISTRY/"
[[ -z "$REGISTRY" ]] && echo "warning: no registry prefix given; images must already be pullable as agentops-*:$TAG" >&2

echo "Deploying tag $TAG to namespace $NAMESPACE on context: $(kubectl config current-context)"
helm upgrade --install agentops "$CHART" \
  --namespace "$NAMESPACE" --create-namespace \
  --values "$CHART/values-aks.yaml" \
  --set image.registry="$REGISTRY" \
  --set image.tag="$TAG" \
  --wait --timeout 10m

kubectl -n "$NAMESPACE" get pods
cat <<EOF

Reach the services with port-forward (nothing is exposed publicly):
  kubectl -n $NAMESPACE port-forward svc/gateway 8000:8000 &
  kubectl -n $NAMESPACE port-forward svc/console-api 8001:8001 &
  kubectl -n $NAMESPACE port-forward svc/console-ui 8501:8501 &
  python scripts/smoke_test.py
EOF
