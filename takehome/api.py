import asyncio
from contextlib import asynccontextmanager, suppress
from time import monotonic
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Response

from takehome.broker import Publisher
from takehome.config import Settings
from takehome.logging import configure_logging, event
from takehome.metrics import Metrics
from takehome.models import AcceptedJob, JobMessage, JobState, SubmitJob
from takehome.storage import JobStore


def create_app(settings=None, store=None, publisher=None):
    settings = settings or Settings()
    store = store or JobStore(settings)
    publisher = publisher or Publisher(settings)
    metrics = Metrics(role="api")

    @asynccontextmanager
    async def lifespan(app):
        configure_logging(settings.log_level)
        task = asyncio.create_task(publisher.run())
        app.state.publisher_task = task
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await publisher.close()
            await store.close()

    app = FastAPI(title="Job API", version="1.0.0", lifespan=lifespan)
    app.state.metrics = metrics
    app.state.store = store
    app.state.publisher = publisher

    @app.middleware("http")
    async def instrument(request, call_next):
        start = monotonic()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            path = route.path if route else "unmatched"
            if path not in ("/livez", "/readyz", "/metrics"):
                method = (
                    request.method
                    if request.method
                    in {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}
                    else "OTHER"
                )
                metrics.requests.labels(method, path, str(status)).inc()
                metrics.latency.labels(method, path).observe(monotonic() - start)

    @app.get("/livez", include_in_schema=False)
    async def live():
        task = getattr(app.state, "publisher_task", None)
        if task is not None and task.done():
            raise HTTPException(503, "publisher loop stopped")
        return {"status": "alive"}

    @app.get("/readyz", include_in_schema=False)
    async def ready():
        try:
            if not publisher.ready or not await store.ping():
                raise ConnectionError
        except Exception:
            raise HTTPException(503, "dependencies unavailable") from None
        return {"status": "ready"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint():
        return metrics.response()

    @app.post("/jobs", response_model=AcceptedJob, status_code=202)
    async def submit(body: SubmitJob, response: Response):
        job_id = uuid4()
        try:
            if not publisher.ready:
                raise ConnectionError
            await store.save(JobState(job_id=job_id, status="queued"))
            await publisher.publish(JobMessage(job_id=job_id, prompt=body.prompt))
        except Exception as exc:
            # Keep queued state on ambiguous confirm timeouts: a worker may own this job.
            event("submission_unavailable", job_id=str(job_id), error_type=type(exc).__name__)
            raise HTTPException(503, "job submission unavailable") from None
        event("job_submitted", job_id=str(job_id))
        response.headers["Location"] = f"/jobs/{job_id}"
        return AcceptedJob(job_id=job_id)

    @app.get("/jobs/{job_id}", response_model=JobState)
    async def retrieve(job_id: UUID):
        try:
            state = await store.get(job_id)
        except Exception as exc:
            event("lookup_unavailable", job_id=str(job_id), error_type=type(exc).__name__)
            raise HTTPException(503, "job storage unavailable") from None
        if state is None:
            raise HTTPException(404, "job not found")
        return state

    return app
