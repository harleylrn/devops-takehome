# Application contract

## HTTP and process interfaces

Run one role per process with `python -m takehome api|worker|mock`. One Uvicorn
process per container is intended; scale using replicas. Bind address defaults to
`0.0.0.0`. Ports: API 8000, worker 8001, mock 8081.

| API route | Behavior |
|---|---|
| `POST /jobs` | Body `{"prompt":"hello"}`; 1–2000 characters, not all whitespace; extra fields rejected. Returns 202 `{"job_id":"<UUID>"}` and Location header only after initial Redis state and broker confirmation. |
| `GET /jobs/{job_id}` | Returns `job_id`, `status`, `result`, `error`. Result/error are null until applicable. Status is queued, running, succeeded, or failed. |
| `/livez` | 200 while the publisher/consumer supervisor is alive; remote outages alone do not cause liveness failure. |
| `/readyz` | API: broker publication channel and Redis. Worker: active consumer channel and Redis. Returns 503 when unavailable. |
| `/metrics` | Prometheus text format; API and worker operations only. |

Unknown UUID: 404. Invalid body/UUID: 422. Dependency failures during submission or
lookup: 503 with sanitized details. All state is refreshed with the configured TTL
on a state write; expired jobs return 404. The mock `POST /infer` accepts `job_id`
and `prompt`, and returns `{"result":"mock-result:<first 16 SHA-256 hex digits of prompt>"}`.
Mock probes report its process health even when its simulated inference endpoint
returns 503. API documentation/OpenAPI is available locally but need not be exposed
through ingress. Expose `/jobs` and its subpaths only; operational routes stay internal.

Example:

```sh
curl -sS -H 'Content-Type: application/json' -d '{"prompt":"hello"}' http://localhost:8000/jobs
curl -sS http://localhost:8000/jobs/REPLACE_WITH_RETURNED_UUID
```

## Delivery, retries, and failure behavior

Both API and worker declare the same durable queue. Messages are persistent and
published with mandatory routing and publisher confirmations. Workers acknowledge
only after saving a terminal result in Redis. Terminal-state redeliveries are
acknowledged without repeating inference. Invalid queue messages are rejected
without requeue; the application never produces these through the public API.

The worker processes one message at a time; prefetch bounds buffered deliveries.
Transport errors, HTTP 429, and 5xx responses are retried with capped exponential
backoff, up to three total attempts by default. Other HTTP failures and invalid
response payloads fail immediately. Terminal errors are `external_unavailable`,
`external_rejected_request`, or `external_invalid_response`.

If Redis fails before a final save, the worker closes its broker session, allowing
unacknowledged work to be redelivered, and waits before reconnecting. It checks
Redis before opening a new consumer. After idle connection loss, the supervisor
reconnects. An idle consumer is healthy; time since last completed job is not a
valid liveness criterion. A readiness failure does **not** itself stop a RabbitMQ
consumer; readiness controls Kubernetes service routing. The worker's own processing
and reconnect logic controls consumption.

SIGTERM stops fetching new jobs and permits the active job to finish for up to
20 seconds. Remaining work is cancelled and redelivered when the connection closes.
Set the pod termination grace above this (for example 30 seconds).

## Known limitations to discuss

- Delivery is at least once, not exactly once. A crash between HTTP execution and
  saving the result can repeat an external call. Use a provider idempotency key or
  stronger deduplication for side-effecting external APIs.
- Redis writes and RabbitMQ publication are not one transaction. A publish timeout
  can leave an orphan queued record or an accepted message despite a 503 response.
  Automatic client retry may create a second job. An outbox and submission
  idempotency contract would address this in a production design.
- Terminal retention bounds deduplication. A message surviving its state TTL can
  be processed again. Status writes are not a distributed lock.
- Persistence depends on the actual Redis/RabbitMQ deployment. A successful Redis
  response is not a claim of disk fsync or high availability. The development
  Compose setup enables Redis AOF and named volumes; it is single-node.
- Malformed messages are discarded, and there is no dead-letter workflow in this
  starter. A permanently unavailable dependency produces failed jobs after bounded
  retries; they are not automatically retried forever.
- The API is unauthenticated and intended for a local exercise. Do not expose it
  publicly without access controls. CPU-based API scaling may not track an
  IO-bound workload well; queue depth/age is useful for worker scaling.

## Environment variables

All roles read `.env` if present; environment values override it. In Kubernetes,
inject settings through configuration and Secret references rather than copying
`.env` into an image. Connection URL values are never logged by application code.

| Variable | Default / meaning |
|---|---|
| `RABBITMQ_URL` | `amqp://localhost:5672/`; replace with a credential-bearing URL from a Secret. Local init creates the real URL. |
| `REDIS_URL` | `redis://localhost:6379/0`; replace with a credential-bearing URL from a Secret. |
| `QUEUE_NAME` | `jobs`; same for API and worker; safe name up to 80 characters. Also namespaces Redis keys. |
| `EXTERNAL_SERVICE_URL` | `http://localhost:8081/infer`; full inference URL. |
| `DEPENDENCY_TIMEOUT_SECONDS` | `3`; Redis socket/connect and broker connect/publish timeouts. |
| `EXTERNAL_TIMEOUT_SECONDS` | `5`; HTTPX per-operation timeout, not a total job deadline. |
| `EXTERNAL_MAX_ATTEMPTS` | `3`; includes the first attempt. |
| `RETRY_BACKOFF_SECONDS` | `0.5`; exponential HTTP retry delay, capped at 10 seconds. |
| `RECONNECT_DELAY_SECONDS` | `2`; delay between consumer/publisher sessions. |
| `WORKER_PREFETCH` | `1`; buffered deliveries per worker, maximum 32. Does not add processing concurrency. |
| `SHUTDOWN_GRACE_SECONDS` | `20`; maximum active-job drain period. |
| `RESULT_TTL_SECONDS` | `86400`; state retention after each write, minimum 60. |
| `HOST` | `0.0.0.0`; process bind address. |
| `APP_API_PORT`, `APP_WORKER_PORT`, `APP_MOCK_PORT` | `8000`, `8001`, `8081`; prefixed to avoid Kubernetes service-link variable collisions. |
| `LOG_LEVEL` | `INFO`; DEBUG, INFO, WARNING, ERROR accepted. |
| `MOCK_MODE` | `normal`; alternatively `slow` or `unavailable`. |
| `MOCK_DELAY_SECONDS` | `8`; delay in slow mode. |

The dependency Compose file additionally uses `RABBITMQ_USER`, `RABBITMQ_PASSWORD`,
and `REDIS_PASSWORD` from the generated local environment. Preserve credentials
when reusing data volumes; changing an environment variable does not necessarily
change an existing broker's stored account.

## Metrics and logs

| Metric | Purpose |
|---|---|
| `api_http_requests_total{method,route,status}` | Traffic and errors; excludes probe/metrics requests. |
| `api_http_request_duration_seconds` | Histogram of API handler latency, not queue-to-result latency. |
| `worker_jobs_total{status}` | Succeeded/failed completions per process. |
| `worker_job_duration_seconds` | Histogram of processing time, including HTTP retries. |
| `worker_consumer_connected` | Current consumer connection state. Redis readiness is checked separately. |
| `worker_jobs_in_progress` | Active delivery processing. |
| `worker_external_retries_total` | Retried inference calls. |

Counters reset with a process restart; use rates/increases. Request labels use
route templates rather than job IDs. Logs are JSON on stdout with event name,
timestamp, job ID where applicable, status, attempt, and safe exception class names.
Prompts, credential URLs, and external response bodies are not logged.

Application metrics do not replace cluster CPU/memory, pod state, or broker queue
metrics. Use your monitoring tools or Kubernetes/RabbitMQ diagnostics for those.
RabbitMQ's built-in Prometheus plugin is an option for queue metrics; see its
[monitoring documentation](https://www.rabbitmq.com/docs/prometheus). Probe semantics
follow [Kubernetes guidance](https://kubernetes.io/docs/concepts/workloads/pods/probes/).
