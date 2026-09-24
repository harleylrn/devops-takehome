#!/usr/bin/env bash
# Remove the entire stack, including PVCs (RabbitMQ/Redis data) and the
# generated secret. Nothing here is reusable across runs by design.
set -euo pipefail

NAMESPACE=takehome

kubectl delete namespace "${NAMESPACE}" --ignore-not-found --wait=true
echo "==> Namespace ${NAMESPACE} removed"
