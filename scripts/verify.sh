#!/usr/bin/env bash
# Wait for all workloads to be ready, then run the supplied smoke test
# against the API through a temporary port-forward.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE=takehome

cd "$REPO_ROOT"

echo "==> Checking rollout status"
kubectl rollout status statefulset/rabbitmq -n "${NAMESPACE}" --timeout=120s
kubectl rollout status statefulset/redis -n "${NAMESPACE}" --timeout=120s
kubectl rollout status deployment/mock -n "${NAMESPACE}" --timeout=120s
kubectl rollout status deployment/api -n "${NAMESPACE}" --timeout=120s
kubectl rollout status deployment/worker -n "${NAMESPACE}" --timeout=120s

echo "==> Port-forwarding api service to run smoke test"
kubectl port-forward -n "${NAMESPACE}" svc/api 18000:8000 >/tmp/takehome-port-forward.log 2>&1 &
PF_PID=$!
trap 'kill "$PF_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 10); do
  if curl -sf http://127.0.0.1:18000/livez >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

uv run python scripts/smoke_test.py --base-url http://127.0.0.1:18000
echo "==> Verify complete: rollout healthy and smoke test passed"
