import asyncio
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from takehome.broker import connect
from takehome.config import Settings
from takehome.logging import configure_logging, event
from takehome.metrics import Metrics
from takehome.models import JobMessage, JobState
from takehome.storage import JobStore


class InferenceFailure(Exception):
    pass


async def infer(client, settings, metrics, job):
    for attempt in range(1, settings.external_max_attempts + 1):
        retryable = False
        try:
            response = await client.post(
                settings.external_service_url,
                json=job.model_dump(mode="json"),
            )
            retryable = response.status_code == 429 or response.status_code >= 500
            response.raise_for_status()
            payload = response.json()
            result = payload.get("result") if isinstance(payload, dict) else None
            if not isinstance(result, str) or len(result) > 4096:
                raise ValueError("invalid inference response")
            return result
        except httpx.TransportError:
            retryable = True
        except httpx.HTTPStatusError:
            if not retryable:
                raise InferenceFailure("external_rejected_request") from None
        except ValueError:
            raise InferenceFailure("external_invalid_response") from None
        if not retryable or attempt == settings.external_max_attempts:
            break
        event("external_retry", job_id=str(job.job_id), attempt=attempt)
        metrics.retries.inc()
        await asyncio.sleep(min(settings.retry_backoff_seconds * 2 ** (attempt - 1), 10))
    raise InferenceFailure("external_unavailable")


async def process_delivery(message, store, client, settings, metrics):
    try:
        job = JobMessage.model_validate_json(message.body)
    except ValidationError:
        event("invalid_message_rejected", service="worker")
        await message.reject(requeue=False)
        return
    existing = await store.get(job.job_id)
    if existing and existing.status in ("succeeded", "failed"):
        event("duplicate_skipped", job_id=str(job.job_id))
        await message.ack()
        return
    with metrics.inflight.track_inprogress(), metrics.processing.time():
        await store.save(JobState(job_id=job.job_id, status="running"))
        event("job_started", job_id=str(job.job_id))
        try:
            result = await infer(client, settings, metrics, job)
            state = JobState(job_id=job.job_id, status="succeeded", result=result)
        except InferenceFailure as exc:
            state = JobState(job_id=job.job_id, status="failed", error=str(exc))
        # A storage error escapes to the supervisor. Close the connection to requeue;
        # do not acknowledge work until its terminal state is durably accepted by Redis.
        await store.save(state)
        await message.ack()
        metrics.jobs.labels(status=state.status).inc()
        event("job_finished", job_id=str(job.job_id), status=state.status)


class Worker:
    def __init__(self, settings, store, metrics):
        self.settings = settings
        self.store = store
        self.metrics = metrics
        self.stop_event = asyncio.Event()
        self.connected = False
        self.connection = None
        self.channel = None
        self.task = None

    @property
    def ready(self):
        return bool(
            self.connected
            and not self.stop_event.is_set()
            and self.channel is not None
            and not self.channel.is_closed
            and self.connection is not None
            and not self.connection.is_closed
            and self.connection.connected.is_set()
        )

    async def next_or_stop(self, iterator):
        next_message = asyncio.create_task(iterator.__anext__())
        stopped = asyncio.create_task(self.stop_event.wait())
        try:
            done, _ = await asyncio.wait(
                (next_message, stopped),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stopped in done:
                return None
            return next_message.result()
        finally:
            for task in (next_message, stopped):
                if not task.done():
                    task.cancel()
            await asyncio.gather(next_message, stopped, return_exceptions=True)

    async def run(self):
        async with httpx.AsyncClient(
            timeout=self.settings.external_timeout_seconds,
            trust_env=False,
        ) as client:
            while not self.stop_event.is_set():
                try:
                    await self.store.ping()
                    self.connection, self.channel, queue = await connect(self.settings)
                    async with queue.iterator() as iterator:
                        self.connected = True
                        self.metrics.connected.set(1)
                        event("consumer_connected", service="worker")
                        while not self.stop_event.is_set():
                            message = await self.next_or_stop(iterator)
                            if message is None:
                                break
                            await process_delivery(
                                message,
                                self.store,
                                client,
                                self.settings,
                                self.metrics,
                            )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    event("consumer_reconnecting", error_type=type(exc).__name__, service="worker")
                finally:
                    self.connected = False
                    self.metrics.connected.set(0)
                    if self.connection is not None:
                        await self.connection.close()
                    self.connection = self.channel = None
                # Sleep before reconnecting even when storage failed mid-delivery.
                try:
                    await asyncio.wait_for(
                        self.stop_event.wait(),
                        self.settings.reconnect_delay_seconds,
                    )
                except TimeoutError:
                    pass

    async def stop(self):
        self.stop_event.set()
        if self.task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self.task), self.settings.shutdown_grace_seconds)
        except TimeoutError:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task


def create_app(settings=None):
    settings = settings or Settings()
    store = JobStore(settings)
    metrics = Metrics()
    worker = Worker(settings, store, metrics)

    @asynccontextmanager
    async def lifespan(app):
        configure_logging(settings.log_level)
        worker.task = asyncio.create_task(worker.run())
        try:
            yield
        finally:
            await worker.stop()
            await store.close()

    app = FastAPI(title="Worker operations", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.worker = worker

    @app.get("/livez")
    async def live():
        if worker.task is None or worker.task.done():
            raise HTTPException(503, "consumer loop stopped")
        return {"status": "alive"}

    @app.get("/readyz")
    async def ready():
        try:
            if not worker.ready or not await store.ping():
                raise ConnectionError
        except Exception:
            raise HTTPException(503, "consumer unavailable") from None
        return {"status": "ready"}

    @app.get("/metrics")
    async def metrics_endpoint():
        # Report current socket/channel state, including idle disconnects.
        metrics.connected.set(int(worker.ready))
        return metrics.response()

    return app
