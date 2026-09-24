"""Run explicitly with RUN_INTEGRATION=1; uses real dependency services."""

import asyncio
import os
import time
from uuid import uuid4

import aio_pika
import httpx
import pytest

from takehome.api import create_app as api_app
from takehome.config import Settings
from takehome.logging import event
from takehome.mock import deterministic_result
from takehome.worker import create_app as worker_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_INTEGRATION") != "1", reason="Set RUN_INTEGRATION=1 with real dependencies"
    ),
]


async def wait_for(check, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if await check():
            return
        await asyncio.sleep(0.1)
    raise TimeoutError("dependency or job did not become ready")


async def test_real_job_and_worker_replacement():
    settings = Settings(queue_name=f"integration-{uuid4().hex}", reconnect_delay_seconds=0.1)
    api = api_app(settings)
    worker = worker_app(settings)
    try:
        async with api.router.lifespan_context(api):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=api), base_url="http://api"
            ) as client:

                async def ready():
                    return (await client.get("/readyz")).status_code == 200

                await wait_for(ready)
                # Submit while no consumer exists, then start a worker.
                response = await client.post("/jobs", json={"prompt": "integration"})
                assert response.status_code == 202
                job_id = response.json()["job_id"]
                assert (await client.get(f"/jobs/{job_id}")).json()["status"] == "queued"
                async with worker.router.lifespan_context(worker):

                    async def completed():
                        result = (await client.get(f"/jobs/{job_id}")).json()
                        assert result["status"] != "failed", result
                        return result["status"] == "succeeded"

                    await wait_for(completed)
                    result = (await client.get(f"/jobs/{job_id}")).json()
                    assert result["result"] == deterministic_result("integration")
                # A new process/consumer can pick up jobs after graceful shutdown.
                replacement = worker_app(settings)
                response = await client.post("/jobs", json={"prompt": "replacement"})
                job_id = response.json()["job_id"]
                async with replacement.router.lifespan_context(replacement):
                    await wait_for(completed)
    finally:
        try:
            connection = await aio_pika.connect(settings.rabbitmq_url.get_secret_value(), timeout=3)
            async with connection:
                channel = await connection.channel()
                await channel.queue_delete(settings.queue_name)
        except Exception as exc:
            # Never let cleanup mask the original failure or print credential URLs
            # from an underlying connection exception's traceback.
            event("integration_queue_cleanup_unavailable", error_type=type(exc).__name__)
