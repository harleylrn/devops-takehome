# DevOps take-home application

Start with [ASSIGNMENT.md](ASSIGNMENT.md). This starter supplies a working application;
your task is its containerization, Kubernetes deployment, and operational tooling.
Stop after four hours and describe unfinished work. An optional cloud deployment
does not earn extra credit.

## Prerequisites

- Python **3.12** (tested with 3.12.11), a container runtime with Docker Compose support,
  and Git. Docker Desktop, or an equivalent supported runtime, is sufficient.
- For your solution: a local Kubernetes distribution, kubectl, and your chosen
  deployment tooling. No cloud or AI-provider account is necessary.
- Internet access is needed initially to download dependencies and images. Running
  the application needs no internet service.
- macOS/Linux or Windows through WSL2; AMD64 and ARM64 are intended targets.
  Start with 4 CPUs and 8 GiB allocated to your runtime; this is a planning budget,
  not a measured minimum. Adjust your monitoring footprint to your laptop.

## First run (application development only)

From the extracted directory:

```sh
git init
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements-dev.lock
python scripts/init_env.py
docker compose -f compose.dependencies.yaml up -d --build --wait
python -m pytest -m 'not integration' -q
RUN_INTEGRATION=1 python -m pytest -m integration -q
```

In two terminals, activate the environment, stay in this directory, and run:

```sh
python -m takehome api
```

```sh
python -m takehome worker
```

In a third terminal:

```sh
python scripts/smoke_test.py --base-url http://127.0.0.1:8000
python scripts/load_test.py --base-url http://127.0.0.1:8000 --jobs 30 --concurrency 5
```

The Compose file runs **only dependencies**. It is a quick way to establish that
the supplied code works before building your own application containers and
Kubernetes deployment. It is not an assignment solution. The mock Dockerfile is
provided; author the API and worker Dockerfiles yourself.

The programs automatically read the ignored `.env` file. `init_env.py` creates
random local passwords with restrictive file permissions and never overwrites an
existing file. Do not use the placeholder values in `.env.example`.

The development API is at port 8000; worker operations at 8001; mock at 8081;
RabbitMQ AMQP at 5672, RabbitMQ management at 15672; Redis at 6379. Compose publishes
dependency ports only on loopback. Keep these dependencies internal in Kubernetes.
Read local management credentials from your `.env`; do not include them in your submission.

## Architecture and operational contract

```text
Client -> Ingress -> API -> RabbitMQ -> Worker -> Mock HTTP service
                     |                   |
                     +----> Redis <------+
```

The API writes initial state and reads results in Redis. The mock represents an
external service but is bundled and deployed internally for this exercise. Explain
real external DNS/egress controls in your README; internet egress is not required
for the local mock.

See [APPLICATION.md](APPLICATION.md) for endpoints, environment configuration,
metrics, delivery behavior, and known limitations. Unit tests do not need running
dependencies. Integration tests use a unique queue, start the API/worker in-process,
and require the real Compose dependencies. Run them in the mock's normal mode.

## Mock controls

```sh
MOCK_MODE=unavailable docker compose -f compose.dependencies.yaml up -d --no-deps mock
# New jobs should reach failed after bounded retries.
MOCK_MODE=slow MOCK_DELAY_SECONDS=8 docker compose -f compose.dependencies.yaml up -d --no-deps mock
# Default HTTP timeout is 5 seconds; slower responses exercise timeouts.
MOCK_MODE=normal docker compose -f compose.dependencies.yaml up -d --no-deps mock
```

For optional prebuilt archives, download the archive matching your runtime's Linux
architecture and verify it against the provided SHA-256 manifest:

```sh
docker load -i devops-takehome-mock-v1.0.0-arm64.tar.gz
docker compose -f compose.dependencies.yaml up -d --no-build --wait
```

Use the `amd64` archive on an AMD64 runtime. Both archives load the same local tag,
`devops-takehome-mock:1.0.0`; do not load both into the same image store. You can
always build `mock/Dockerfile` from this directory instead.

## Cleanup

Stop the two Python processes with Ctrl-C. This removes local dependency containers
and their exercise data volumes:

```sh
docker compose -f compose.dependencies.yaml down -v
```

You are responsible for documenting cleanup of your own deployment. See the
submission checklist in the assignment before handing in your repository.

## Solution

### Running it

```sh
./scripts/setup.sh      # build images, generate secrets, deploy to current kubectl context
./scripts/verify.sh     # wait for rollout, run scripts/smoke_test.py through the api Service
./scripts/teardown.sh   # delete the takehome namespace (PVCs and secret included)
```

Assumes a local Kubernetes cluster (kind/k3d/minikube) with an nginx ingress
controller and, for kind, a `localhost:5000` registry (or use `kind load
docker-image`, which `setup.sh` does automatically if it detects a kind cluster).

### Architecture

```
Client -> Ingress (path: /jobs) -> api Deployment (2) -> RabbitMQ StatefulSet -> worker Deployment (2) -> mock Deployment
                                        |                                            |
                                        +------------------ Redis StatefulSet -------+
```

- `k8s/base/` — namespace, ConfigMap, Secret template, RabbitMQ/Redis StatefulSets
  (need stable network identity + PVCs), api/worker/mock Deployments, Services, Ingress.
- `docker/api.Dockerfile`, `docker/worker.Dockerfile` — mirror the supplied
  `mock/Dockerfile` pattern exactly (slim base, hash-pinned deps, non-root UID 10001).
- Plain manifests, not Helm/Kustomize — fastest to write and review correctly inside
  the time box. Helm is what I'd reach for beyond a single environment.

### Networking

- Only `/jobs` (POST + GET `{id}`) is on the Ingress. RabbitMQ, Redis, mock, worker,
  and the api's own `/livez` `/readyz` `/metrics` are ClusterIP-only — never routed
  through Ingress, reachable in-cluster or via `kubectl port-forward`.
- Service discovery via cluster DNS (`rabbitmq.takehome.svc.cluster.local`, etc.),
  wired into `app-config`/`app-secrets` at deploy time — nothing hardcoded in the app.
- Production egress/isolation I'd add but didn't build here (time budget): default-deny
  NetworkPolicy per namespace with explicit allows (api→rabbitmq, api→redis,
  worker→rabbitmq, worker→redis, worker→mock/real-external), TLS via cert-manager on
  the Ingress, and DNS egress locked to only the external inference host in production
  (the mock needs no internet).

### Security

- Non-root containers (UID 10001), hash-pinned dependency installs (`--require-hashes`).
- Credentials (`RABBITMQ_PASSWORD`, `REDIS_PASSWORD`, composed `RABBITMQ_URL`/`REDIS_URL`)
  live only in the `app-secrets` Secret, generated fresh by `scripts/setup.sh` via
  `openssl rand -hex 24` — never written to disk or committed. `k8s/base/secret-template.yaml`
  documents the shape only; it is explicitly not meant to be applied.
- Resource requests/limits set on every container to bound noisy-neighbor blast radius.

### CI/CD

`ci/Jenkinsfile`: checkout → `uv sync --frozen --extra dev` + `ruff check` + unit tests →
build images tagged with the short git SHA → publish (kind: `kind load docker-image`;
real registry: `docker push` — same branch `scripts/setup.sh` uses) → `kubectl apply` +
`kubectl set image` → rollout status wait on every workload → `scripts/verify.sh` (runs
the supplied `smoke_test.py` through a port-forward). Any stage failure fails the
pipeline — there's no "deploy anyway" branch.

**Demonstrated locally** by running every stage's exact command by hand against this
kind cluster, no hosted Jenkins involved (not claiming a real CI run):

```
Lint & Unit Test  -> ruff: all checks passed; pytest: 28 passed
Build Images      -> 3 images built, tagged localhost:5000/takehome-{api,worker,mock}:local090928
Publish Images     -> kind load docker-image x3 (kind cluster detected, no registry needed)
Deploy             -> kubectl apply (idempotent) + kubectl set image x3
Rollout Check      -> mock, api, worker all "successfully rolled out"
Smoke Test         -> PASS: validation, lookup, submission, and completion
```

Two real bugs surfaced and fixed by actually running it end to end (not just reading the
YAML): `uv sync --frozen` alone strips pytest/ruff — they're declared under
`[project.optional-dependencies].dev`, an extra, not synced by default; needs
`--extra dev`. And the pipeline's `Push Images` stage unconditionally ran `docker push`,
which has nothing to push to against a kind cluster with no registry — fixed to branch
the same way `scripts/setup.sh` does (kind-load vs. push) and renamed to `Publish Images`.

### Observability

App exposes 6 Prometheus metrics already (`api_http_requests_total`,
`api_http_request_duration_seconds`, `worker_jobs_total`, `worker_job_duration_seconds`,
`worker_consumer_connected`, `worker_jobs_in_progress`) plus JSON logs on stdout.
`observability/prometheus-config.yaml` deploys a minimal in-cluster Prometheus scraping
both. `observability/queries.md` has the PromQL + `kubectl` commands for error rate,
p95 latency, worker health, queue depth, and one combined "is it broken" view (pod
status + queue depth in a single command).

**Verified live, not just deployed.** First version used `static_configs` pointed at the
`api`/`worker` Service DNS names. Checking `/api/v1/targets` after rollout showed only
2 active targets total (1 per job) despite 2 replicas each — a ClusterIP Service
load-balances to one backend per connection, so scraping it only ever reaches one
replica; the other pod's metrics were invisible, and which pod answers can change
scrape-to-scrape (misleading counters). Fixed to `kubernetes_sd_configs` with
`role: pod` (needs a ServiceAccount + ClusterRole/Binding to list/watch pods, both in
the manifest), filtered to `app in (api, worker)`. Re-checked after the fix: 4 active
targets, one per actual pod, all `up`. Queried `api_http_requests_total`,
`worker_jobs_total`, `worker_consumer_connected`, `worker_jobs_in_progress` directly
against Prometheus afterward and got real series back, not just "target up".

**Pod CPU/memory:** kind has no metrics-server by default. Installed it (see
`observability/queries.md`) with the `--kubelet-insecure-tls` patch kind requires — its
kubelet serving certs aren't signed by a CA metrics-server trusts out of the box, and
without the patch `kubectl top` fails with `Metrics API not available` even though the
metrics-server pod itself is `Ready`. After the patch, `kubectl top pods -n takehome`
returns real per-pod CPU/memory numbers.

### Resilience & scaling

Both demonstrated live against the `kind` cluster through the Ingress (`http://localhost/jobs`).

**Worker restart mid-job.** Submitted a job, tailed both worker pods' logs to find which one
picked it up (`job_started` event), then force-killed it while the mock call was still in
flight:

```sh
kubectl delete pod worker-7887df984f-fdrkj -n takehome --grace-period=0 --force
curl -s http://localhost/jobs/83cc5286-a88c-4eb9-950b-a6c04565991e
# {"status":"running", ...}   <- right after the kill, message not yet redelivered
# ~2s later:
# {"status":"succeeded","result":"mock-result:c2e6bb8b0d13719c", ...}
```

RabbitMQ detected the dropped consumer, requeued the unacked message, and the surviving
worker picked it up and finished it. Terminal-state idempotency in `storage.py`/`worker.py`
guarantees this redelivery path never double-processes a job that already reached
`succeeded`/`failed`.

Note: same test with the mock's `MOCK_DELAY_SECONDS` set above `EXTERNAL_TIMEOUT_SECONDS`
correctly ends in `failed`/`external_unavailable` after retries — that's the timeout
contract working as documented in APPLICATION.md, not a resilience failure.

**Manual scaling under load.** Baseline: scaled `worker` to 1 replica, put the mock in
`slow` mode (`MOCK_DELAY_SECONDS=3`), then fired a burst:

```sh
kubectl scale deployment/worker -n takehome --replicas=1
python3 scripts/load_test.py --base-url http://localhost --jobs 20 --concurrency 10
# {"succeeded": 20, "failed": 0, "submission_errors": 0, "timed_out": 0, "elapsed_seconds": 60.88}
```

Queue backed up as expected (checked via the RabbitMQ management HTTP API — `rabbitmqctl`
has ~60s of per-invocation startup overhead in this image, too slow for live polling, see
`observability/queries.md`):

```sh
curl -s -u "$USER:$PASS" http://127.0.0.1:15672/api/queues/%2F/jobs
# messages=10 ready=9 unacked=1   (single worker, prefetch=1)
```

Scaled to 4 replicas mid-burst — 4 consumers attached immediately, backlog drained to zero
within seconds, and the re-run finished faster end to end:

```sh
kubectl scale deployment/worker -n takehome --replicas=4
# messages=7 -> 2 -> 0 over the next few seconds
# {"succeeded": 20, "failed": 0, "submission_errors": 0, "timed_out": 0, "elapsed_seconds": 47.85}
```

Restored afterward to the manifests' declared state: `worker` replicas=2, `MOCK_MODE=normal`,
`EXTERNAL_TIMEOUT_SECONDS=5`.

Terminal-state idempotency in `storage.py`/`worker.py` means redelivery after a pod
kill never double-processes a completed job.

### Decisions & limitations

- StatefulSets for RabbitMQ/Redis (1 replica each) — correct primitive for storage
  identity, but single-node, so no HA for the broker/state store in this exercise.
  Production: RabbitMQ cluster (3 nodes, quorum queues) or a managed broker; Redis
  Sentinel/cluster or a managed instance.
- No NetworkPolicy shipped — optional per the assignment, cut for time; see
  Networking section above for what production needs.
- No HPA — manual `kubectl scale` demonstrated instead; a queue-depth-driven KEDA
  ScaledObject on the worker Deployment would be the production version.
- `observability/queries.md` uses the RabbitMQ management HTTP API for queue depth,
  not `rabbitmqctl`. Discovered during the resilience demo above: each `rabbitmqctl`
  invocation in this image spins up a fresh Erlang node and takes ~60s to return, which
  makes it useless for live polling — the management API (already exposed on 15672,
  same credentials as `app-secrets`) responds in milliseconds.
- `secret-template.yaml` exists for documentation only; real secrets are always
  generated fresh, per-cluster, by `scripts/setup.sh`.

### Time spent

~4 hours: containerization + K8s manifests (~1.5h), networking/ingress (~0.3h),
CI/CD Jenkinsfile (~0.75h), observability (~0.5h), resilience/scaling demo (~0.3h),
scripts + this writeup (~0.5h). Left undone: NetworkPolicies, HPA/KEDA, TLS,
multi-node RabbitMQ/Redis — called out above rather than half-built.

### Production improvements (not built, discussed for the live review)

API HPA on request rate/CPU; queue-depth-driven worker autoscaling (KEDA);
NetworkPolicies with CNI enforcement proof; mTLS/workload identity between services;
RabbitMQ/Redis HA with backup and restore drills; progressive rollout (canary/blue-green)
with automatic rollback on smoke-test or error-rate regression.
