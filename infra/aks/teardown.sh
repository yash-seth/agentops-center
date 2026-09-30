#!/usr/bin/env bash
# Delete EVERYTHING created by create-cluster.sh (the whole resource group) so nothing keeps billing.
#
#   ./infra/aks/teardown.sh            # asks you to type the resource group name
#   ./infra/aks/teardown.sh --yes      # non-interactive (CI or scripts)
#
# It only ever deletes the resource group named below, and only if it carries the
# purpose=agentops-demo tag that create-cluster.sh sets, so it cannot remove an unrelated group.
set -euo pipefail

RG="${AOC_AKS_RG:-agentops-demo-rg}"
ASSUME_YES="no"
[[ "${1:-}" == "--yes" ]] && ASSUME_YES="yes"

command -v az >/dev/null || { echo "Azure CLI (az) is required" >&2; exit 1; }
az account show >/dev/null 2>&1 || { echo "run 'az login' first" >&2; exit 1; }

if [[ "$(az group exists --name "$RG")" != "true" ]]; then
  echo "Resource group '$RG' does not exist. Nothing to delete."
  exit 0
fi

PURPOSE="$(az group show --name "$RG" --query 'tags.purpose' -o tsv)"
if [[ "$PURPOSE" != "agentops-demo" ]]; then
  echo "Refusing to delete '$RG': it is not tagged purpose=agentops-demo (tag is '$PURPOSE')." >&2
  exit 2
fi

echo "Resources that will be deleted from '$RG':"
az resource list --resource-group "$RG" --query '[].{name:name,type:type}' -o table

if [[ "$ASSUME_YES" != "yes" ]]; then
  read -r -p "Type the resource group name to confirm deletion: " typed
  [[ "$typed" == "$RG" ]] || { echo "name did not match; aborted"; exit 1; }
fi

az group delete --name "$RG" --yes
echo "Deleted. Confirming: exists=$(az group exists --name "$RG")"
echo "Also check the Azure portal (Cost Management) that no charges continue."
