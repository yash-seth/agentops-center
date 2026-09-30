# Deploying to Azure Kubernetes Service (optional)

The whole project runs locally for free (kind). AKS is an optional extra to show a real cloud
deployment. **It creates billable resources, so tear it down the same day.**

## Cost, honestly
- The AKS control plane uses the **free tier**. You pay for the node VM (one `Standard_B2s`), its disk,
  and network egress. For a few hours that is cents to a few dollars; left running for a month it is
  tens of dollars. Check current prices in the Azure pricing calculator for your region.
- A new Azure account's free trial credit is enough to cover a short session. Set a cost alert in the
  Azure portal before you start.
- No paid add-ons are enabled (no Azure Monitor, Defender or load balancer with a public IP; access
  is by `kubectl port-forward`).

## Steps
```bash
az login
./infra/aks/create-cluster.sh                 # asks for confirmation; ~5-10 minutes
./infra/aks/deploy.sh 0.1.0 ghcr.io/<you>/    # tag + registry prefix of the released images
# port-forward as printed by deploy.sh, then:
python scripts/smoke_test.py
./infra/aks/teardown.sh                       # deletes the whole resource group
```

## Prerequisites
- Azure CLI, kubectl, helm.
- Images published by `.github/workflows/release.yml` (tag `v0.1.0` gives image tag `0.1.0`). GHCR
  packages must be **public** for the cluster to pull them anonymously; otherwise create an image pull
  secret and reference it from the chart.
- Quota for one `Standard_B2s` VM in the chosen region (`AOC_AKS_LOCATION`, default `centralindia`).
  If creation fails on quota or availability, set `AOC_AKS_NODE_SIZE` or `AOC_AKS_LOCATION`.

## Safety
- `teardown.sh` deletes only the resource group named in `AOC_AKS_RG`, and only if it carries the
  `purpose=agentops-demo` tag that `create-cluster.sh` sets. It requires you to type the group name
  unless `--yes` is given.
- The chart defaults keep the chaos API off and services private (`ClusterIP`).

## What is not verified
These scripts were written without access to an Azure subscription and have not been run. Treat the
first run as a supervised one, and read the `az` output.

## Manual deploy from GitHub Actions
`.github/workflows/deploy-aks.yml` deploys a released tag using OIDC (no stored cloud passwords). It is
manual-only. Configure a federated credential for the repository and add the repository secrets
`AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and `AZURE_SUBSCRIPTION_ID`, plus a protected `aks` environment.
