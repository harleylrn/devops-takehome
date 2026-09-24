# Senior DevOps Engineer Take-Home Exercise

## Objective and time limit

Turn the supplied application into reliable, secure, observable, and repeatable
infrastructure. Spend **3–4 hours; stop at four hours**. Record time spent, what you
completed, and what you would tackle next. We assess prioritization and reasoning,
not how much unpaid time you can spend on infrastructure.

You may use documentation, search engines, AI coding assistants, and other tools.
Be prepared to explain and modify your implementation during the review.

## Environment

Local Kubernetes is the default and fully supported option: kind, k3d, minikube,
or another local distribution. Use your preferred container runtime and deployment
tools. A local image registry is acceptable. No cloud account is required.

You may use an existing personal cloud sandbox, but this provides no scoring
advantage. Keep resources small, remove them afterward, and do not send credentials.
Prepare prerequisites before starting the four-hour work session; report significant
setup blockers separately.

## Application

We supply working Python API, worker, mock service, instrumentation, and tests.
The API validates submissions, stores initial state in Redis, and publishes jobs to
RabbitMQ. The worker calls the mock and stores results in Redis. The API retrieves
those results. The mock runs as an internal fifth workload and simulates an external
dependency. Read APPLICATION.md before choosing probes and network access rules.

Application changes are allowed with a rationale, but are not expected. Preserve
the documented HTTP contract and include the original tests in your submission.

## Required implementation

1. **Containerization and Kubernetes:** author API and worker Dockerfiles; deploy
   the API, worker, RabbitMQ, Redis, and supplied mock. Use Deployments/StatefulSets,
   Services, configuration, Secrets, requests/limits, appropriate probes, restart
   behavior, and deliberate replica counts. Helm, Kustomize, or plain manifests
   are equally acceptable.
2. **Networking:** expose job API routes through ingress. Keep the broker, Redis,
   mock, worker, and operational endpoints internal. Account for internal DNS and
   service discovery. Explain production ingress, egress, isolation, and DNS
   controls. If you include NetworkPolicies, say whether your CNI enforces them
   and show how you checked; creating objects alone is not proof of enforcement.
3. **CI/CD:** provide a pipeline for checkout, application tests, image builds,
   immutable tagging, registry push, deployment, rollout checks, and the supplied
   smoke test. It must fail on an unhealthy deployment. Jenkins is preferred;
   another platform is acceptable if you explain the Jenkins equivalent. Running
   Jenkins is optional: demonstrate the executable steps locally and document
   the runner/tools/network/credentials the pipeline would need. Do not claim an
   actual CI run if only local steps were executed.
4. **Observability:** demonstrate API availability, latency and error rate; pod
   health, CPU/memory; worker health; queue depth; and application logs. A small
   collection of queries/commands is sufficient, with at least one useful health
   view. A Grafana dashboard or centralized logging installation is optional.
5. **Resilience and scaling:** demonstrate a worker restart and manual worker
   scaling, with jobs still completing. Explain resources, queue growth, retries,
   dependency failures, rolling updates, and unhealthy releases.
6. **Reproducibility:** supply scripts and declarative configuration so another
   engineer can recreate and remove your environment. Terraform is optional for
   local Kubernetes and preferred where it adds value for cloud infrastructure.

## Discussion topics; implementation optional

Discuss API HPA and queue-driven worker scaling, enforced NetworkPolicies, external
egress, production identity/TLS, stateful-service availability, persistence/backups,
and rollback. Cloud architecture can be discussed without deploying cloud resources.
Extra tools and optional features do not earn automatic bonus points.

## Submission checklist

Submit a Git repository containing the supplied application plus your:

- [ ] API/worker Dockerfiles and deployment configuration.
- [ ] Pipeline definition and locally executed steps or actual CI evidence.
- [ ] Setup, deployment, verification, and cleanup scripts/instructions.
- [ ] Observability queries/view and example diagnostic commands.
- [ ] Worker restart and scaling evidence.
- [ ] Concise README covering architecture, networking, observability, scaling,
      security, decisions, limitations, time spent, and production improvements.
- [ ] No credentials, kubeconfigs, `.env`, generated state, or private cloud keys.

Screenshots are optional; commands and short sanitized output are sufficient.
If incomplete, submit what works and state exactly what remains unverified.

## Evaluation and live review

The take-home accounts for **50 points**: deployment/networking/reliability/security
(25), CI/CD (10), observability (10), and reproducibility/clarity (5).

The live review accounts for **50 points**: diagnosis and recovery (20), explanation
and ownership (15), a small deployment/configuration change (10), and production
tradeoffs (5). You will walk through the application, configuration, pipeline,
logs/metrics, scaling, and security choices. We will introduce a controlled failure
and ask you to diagnose it. We assess evidence-led troubleshooting, not memorized
commands. All candidates are evaluated against the same categories.
