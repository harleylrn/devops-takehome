# Observability: queries & commands

View: `kubectl port-forward -n takehome svc/prometheus 9090:9090`, then open http://127.0.0.1:9090.

## API availability / latency / error rate

```promql
sum(rate(api_http_requests_total{status=~"5.."}[5m])) / sum(rate(api_http_requests_total[5m]))
histogram_quantile(0.95, sum(rate(api_http_request_duration_seconds_bucket[5m])) by (le))
sum(rate(api_http_requests_total[5m])) by (status)
```

## Worker health

```promql
worker_consumer_connected
worker_jobs_in_progress
sum(rate(worker_jobs_total[5m])) by (status)
sum(rate(worker_external_retries_total[5m]))
histogram_quantile(0.95, sum(rate(worker_job_duration_seconds_bucket[5m])) by (le))
```

## Pod health / CPU / memory

Needs metrics-server. Not in kind by default — install with:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl patch deployment metrics-server -n kube-system --type='json' \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
```

The `--kubelet-insecure-tls` patch is required on kind: its kubelet serving certs aren't
signed by a CA metrics-server trusts by default, and without the patch metrics-server's
pods report `Ready` but every `kubectl top` call fails with `error: Metrics API not
available` until it's applied.

```bash
kubectl top pods -n takehome
kubectl get pods -n takehome -o wide
kubectl describe pod -n takehome -l app=worker
```

## Queue depth (RabbitMQ management API)

`rabbitmqctl` has ~60s of per-invocation startup overhead in this image (spins up a
fresh Erlang node each call) — too slow for live polling. Use the management HTTP API
instead:

```bash
kubectl port-forward -n takehome svc/rabbitmq 15672:15672 &
USER=$(kubectl get secret app-secrets -n takehome -o jsonpath='{.data.RABBITMQ_USER}' | base64 -d)
PASS=$(kubectl get secret app-secrets -n takehome -o jsonpath='{.data.RABBITMQ_PASSWORD}' | base64 -d)
curl -s -u "$USER:$PASS" http://127.0.0.1:15672/api/queues/%2F/jobs \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["messages"], d["messages_ready"], d["messages_unacknowledged"])'
```

## Application logs (structured JSON on stdout)

```bash
kubectl logs -n takehome -l app=api --since=10m
kubectl logs -n takehome -l app=worker --since=10m | jq 'select(.level=="ERROR")'
```

## One useful health view

```bash
kubectl get pods -n takehome -o wide && \
curl -s -u "$USER:$PASS" http://127.0.0.1:15672/api/queues/%2F/jobs | python3 -m json.tool | grep messages
```
Gives pod status + restarts + queue depth in one shot — the fastest way to tell "is the pipeline backed up or broken."
