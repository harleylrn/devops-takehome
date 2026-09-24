#!/usr/bin/env bash
# Build images, generate cluster secrets, and deploy the full stack to
# whatever kubectl context is currently active (local kind/k3d/minikube).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE=takehome
REGISTRY=localhost:5000
TAG="${TAG:-latest}"

cd "$REPO_ROOT"

echo "==> Building images"
docker build -t "${REGISTRY}/takehome-api:${TAG}" -f docker/api.Dockerfile .
docker build -t "${REGISTRY}/takehome-worker:${TAG}" -f docker/worker.Dockerfile .
docker build -t "${REGISTRY}/takehome-mock:${TAG}" -f mock/Dockerfile .

if command -v kind >/dev/null 2>&1 && kind get clusters 2>/dev/null | grep -q .; then
  KIND_CLUSTER="$(kind get clusters | head -n1)"
  echo "==> Loading images into kind cluster: ${KIND_CLUSTER}"
  kind load docker-image "${REGISTRY}/takehome-api:${TAG}" --name "${KIND_CLUSTER}"
  kind load docker-image "${REGISTRY}/takehome-worker:${TAG}" --name "${KIND_CLUSTER}"
  kind load docker-image "${REGISTRY}/takehome-mock:${TAG}" --name "${KIND_CLUSTER}"
else
  echo "==> No kind cluster detected, pushing to ${REGISTRY}"
  docker push "${REGISTRY}/takehome-api:${TAG}"
  docker push "${REGISTRY}/takehome-worker:${TAG}"
  docker push "${REGISTRY}/takehome-mock:${TAG}"
fi

echo "==> Applying namespace"
kubectl apply -f k8s/base/namespace.yaml

echo "==> Generating app-secrets (not committed, regenerated each run)"
RABBITMQ_PASSWORD="$(openssl rand -hex 24)"
REDIS_PASSWORD="$(openssl rand -hex 24)"
kubectl create secret generic app-secrets \
  --namespace "${NAMESPACE}" \
  --from-literal=RABBITMQ_USER=takehome \
  --from-literal=RABBITMQ_PASSWORD="${RABBITMQ_PASSWORD}" \
  --from-literal=REDIS_PASSWORD="${REDIS_PASSWORD}" \
  --from-literal=RABBITMQ_URL="amqp://takehome:${RABBITMQ_PASSWORD}@rabbitmq.${NAMESPACE}.svc.cluster.local:5672/" \
  --from-literal=REDIS_URL="redis://:${REDIS_PASSWORD}@redis.${NAMESPACE}.svc.cluster.local:6379/0" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> Applying config and workloads"
kubectl apply -f k8s/base/configmap.yaml
kubectl apply -f k8s/base/rabbitmq.yaml
kubectl apply -f k8s/base/redis.yaml

echo "==> Waiting for RabbitMQ and Redis before starting api/worker"
kubectl rollout status statefulset/rabbitmq -n "${NAMESPACE}" --timeout=120s
kubectl rollout status statefulset/redis -n "${NAMESPACE}" --timeout=120s

kubectl apply -f k8s/base/mock.yaml
kubectl apply -f k8s/base/api.yaml
kubectl apply -f k8s/base/worker.yaml
kubectl apply -f k8s/base/ingress.yaml

echo "==> Setup complete. Run scripts/verify.sh to confirm rollout and smoke test."
