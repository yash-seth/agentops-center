#!/usr/bin/env bash
# Create a small, cheap AKS cluster for a demo. Read infra/aks/README.md first (cost + teardown).
#
#   az login
#   ./infra/aks/create-cluster.sh
#
# Everything lives in one resource group so teardown.sh can remove all of it in one step.
set -euo pipefail

RG="${AOC_AKS_RG:-agentops-demo-rg}"
LOCATION="${AOC_AKS_LOCATION:-centralindia}"
CLUSTER="${AOC_AKS_CLUSTER:-agentops-demo}"
NODE_SIZE="${AOC_AKS_NODE_SIZE:-Standard_B2s}"   # 2 vCPU / 4 GiB; check quota and price for your region

command -v az >/dev/null || { echo "Azure CLI (az) is required" >&2; exit 1; }
az account show >/dev/null 2>&1 || { echo "run 'az login' first" >&2; exit 1; }

echo "Subscription : $(az account show --query name -o tsv)"
echo "Resource group: $RG ($LOCATION)   Cluster: $CLUSTER   Node size: $NODE_SIZE (1 node)"
echo "This creates billable resources. Tear down with ./infra/aks/teardown.sh when you are done."
read -r -p "Continue? [y/N] " answer
[[ "$answer" == "y" || "$answer" == "Y" ]] || { echo "aborted"; exit 1; }

az group create --name "$RG" --location "$LOCATION" \
  --tags purpose=agentops-demo created-by="$(az account show --query user.name -o tsv)" >/dev/null

# Free control-plane tier, a single small node, no paid add-ons (Azure Monitor, Defender, ...).
az aks create \
  --resource-group "$RG" \
  --name "$CLUSTER" \
  --tier free \
  --node-count 1 \
  --node-vm-size "$NODE_SIZE" \
  --nodepool-name system \
  --enable-managed-identity \
  --generate-ssh-keys \
  --tags purpose=agentops-demo

az aks get-credentials --resource-group "$RG" --name "$CLUSTER" --overwrite-existing
kubectl get nodes
echo "Cluster ready. Deploy with ./infra/aks/deploy.sh <image-tag>"
