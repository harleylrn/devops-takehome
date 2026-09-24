from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.responses import Response


class Metrics:
    def __init__(self, role="worker"):
        if role not in ("api", "worker"):
            raise ValueError("metrics role must be api or worker")
        self.registry = CollectorRegistry()
        api_registry = self.registry if role == "api" else None
        worker_registry = self.registry if role == "worker" else None
        self.requests = Counter(
            "api_http_requests_total",
            "API responses",
            ["method", "route", "status"],
            registry=api_registry,
        )
        self.latency = Histogram(
            "api_http_request_duration_seconds",
            "API handler latency",
            ["method", "route"],
            registry=api_registry,
        )
        self.jobs = Counter(
            "worker_jobs_total",
            "Terminal job outcomes",
            ["status"],
            registry=worker_registry,
        )
        self.processing = Histogram(
            "worker_job_duration_seconds",
            "Job processing duration",
            registry=worker_registry,
        )
        self.connected = Gauge(
            "worker_consumer_connected",
            "Consumer session active (not a readiness check)",
            registry=worker_registry,
        )
        self.inflight = Gauge(
            "worker_jobs_in_progress",
            "Currently executing deliveries",
            registry=worker_registry,
        )
        self.retries = Counter(
            "worker_external_retries_total",
            "External HTTP retries",
            registry=worker_registry,
        )
        for status in ("succeeded", "failed"):
            self.jobs.labels(status=status).inc(0)

    def response(self):
        return Response(
            generate_latest(self.registry), headers={"Content-Type": CONTENT_TYPE_LATEST}
        )
