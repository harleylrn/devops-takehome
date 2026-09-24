import asyncio
import json
import logging
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from takehome.api import create_app
from takehome.config import Settings
from takehome.logging import JsonFormatter
from takehome.metrics import Metrics
from takehome.mock import create_app as mock_app
from takehome.mock import deterministic_result
from takehome.models import JobMessage
from takehome.worker import InferenceFailure, Worker, infer, process_delivery


class MemoryStore:
    def __init__(self):
        self.states = {}
        self.available = True

    async def ping(self):
        if not self.available:
            raise ConnectionError
        return True

    async def save(self, state):
        await self.ping()
        self.states[state.job_id] = state

    async def get(self, job_id):
        await self.ping()
        return self.states.get(job_id)

    async def close(self):
        pass


class FakePublisher:
    ready = True

    def __init__(self):
        self.publish = AsyncMock()

    async def run(self):
        await asyncio.Event().wait()

    async def close(self):
        pass


@pytest.fixture
def api():
    store, publisher = MemoryStore(), FakePublisher()
    app = create_app(Settings(_env_file=None), store, publisher)
    with TestClient(app) as client:
        yield client, store, publisher


def test_submit_persists_before_publish_and_retrieves(api):
    client, store, publisher = api

    async def publish(job):
        assert store.states[job.job_id].status == "queued"

    publisher.publish.side_effect = publish
    response = client.post("/jobs", json={"prompt": "hello"})
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert response.headers["Location"] == f"/jobs/{job_id}"
    assert client.get(f"/jobs/{job_id}").json() == {
        "job_id": job_id,
        "status": "queued",
        "result": None,
        "error": None,
    }


@pytest.mark.parametrize(
    "payload",
    [{}, {"prompt": ""}, {"prompt": " "}, {"prompt": "x" * 2001}, {"prompt": "x", "extra": True}],
)
def test_validation(api, payload):
    client, _, publisher = api
    assert client.post("/jobs", json=payload).status_code == 422
    publisher.publish.assert_not_awaited()


def test_unknown_and_invalid_ids(api):
    client, _, _ = api
    assert client.get(f"/jobs/{uuid4()}").status_code == 404
    assert client.get("/jobs/not-a-uuid").status_code == 422


@pytest.mark.parametrize("failure", ["broker", "redis", "confirm"])
def test_submission_never_claims_success_when_unavailable(api, failure):
    client, store, publisher = api
    if failure == "broker":
        publisher.ready = False
    elif failure == "redis":
        store.available = False
    else:
        publisher.publish.side_effect = TimeoutError
    assert client.post("/jobs", json={"prompt": "hello"}).status_code == 503
    assert client.get("/livez").status_code == 200
    if failure != "confirm":
        assert client.get("/readyz").status_code == 503


def test_metrics_use_route_templates_not_ids(api):
    client, _, _ = api
    job_id = str(uuid4())
    client.get(f"/jobs/{job_id}")
    client.get("/arbitrary-private-path")
    metrics = client.get("/metrics").text
    assert 'route="/jobs/{job_id}"' in metrics
    assert 'route="unmatched"' in metrics
    assert job_id not in metrics
    assert "/arbitrary-private-path" not in metrics
    assert "worker_consumer_connected" not in metrics


def test_worker_metrics_do_not_include_api_instruments():
    output = Metrics(role="worker").response().body.decode()
    assert "worker_consumer_connected" in output
    assert "api_http" not in output


async def test_mock_is_deterministic():
    job = JobMessage(job_id=uuid4(), prompt="hello")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mock_app(Settings(_env_file=None))),
        base_url="http://mock",
    ) as client:
        first = await client.post("/infer", json=job.model_dump(mode="json"))
        second = await client.post("/infer", json=job.model_dump(mode="json"))
    assert first.json() == second.json() == {"result": deterministic_result("hello")}


async def test_retries_then_success():
    calls = []

    def responder(request):
        calls.append(request)
        return httpx.Response(503 if len(calls) < 3 else 200, json={"result": "ok"})

    settings = Settings(_env_file=None, retry_backoff_seconds=0.001)
    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        assert (
            await infer(client, settings, Metrics(), JobMessage(job_id=uuid4(), prompt="x")) == "ok"
        )
    assert len(calls) == 3


@pytest.mark.parametrize(
    "code,attempts,error",
    [
        (503, 3, "external_unavailable"),
        (429, 3, "external_unavailable"),
        (400, 1, "external_rejected_request"),
    ],
)
async def test_bounded_http_failures(code, attempts, error):
    calls = []

    def responder(request):
        calls.append(request)
        return httpx.Response(code)

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        with pytest.raises(InferenceFailure, match=error):
            await infer(
                client,
                Settings(_env_file=None, retry_backoff_seconds=0.001),
                Metrics(),
                JobMessage(job_id=uuid4(), prompt="x"),
            )
    assert len(calls) == attempts


async def test_timeout_is_bounded():
    calls = []

    def responder(request):
        calls.append(request)
        raise httpx.ReadTimeout("timeout")

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        with pytest.raises(InferenceFailure, match="external_unavailable"):
            await infer(
                client,
                Settings(_env_file=None, retry_backoff_seconds=0.001),
                Metrics(),
                JobMessage(job_id=uuid4(), prompt="x"),
            )
    assert len(calls) == 3


class Delivery:
    def __init__(self, job):
        self.body = job.model_dump_json().encode()
        self.ack = AsyncMock()
        self.reject = AsyncMock()


async def test_ack_only_after_terminal_save_and_skip_redelivery():
    store = MemoryStore()
    job = JobMessage(job_id=uuid4(), prompt="hello")
    message = Delivery(job)
    calls = []

    async def ack():
        assert store.states[job.job_id].status == "succeeded"

    message.ack.side_effect = ack

    def responder(request):
        calls.append(request)
        return httpx.Response(200, json={"result": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        for _ in range(2):
            await process_delivery(message, store, client, Settings(_env_file=None), Metrics())
    assert len(calls) == 1
    assert message.ack.await_count == 2


async def test_failed_job_saved_and_acknowledged():
    store = MemoryStore()
    job = JobMessage(job_id=uuid4(), prompt="hello")
    message = Delivery(job)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(503))
    ) as client:
        await process_delivery(
            message, store, client, Settings(_env_file=None, retry_backoff_seconds=0.001), Metrics()
        )
    assert store.states[job.job_id].status == "failed"
    assert store.states[job.job_id].error == "external_unavailable"
    message.ack.assert_awaited_once()


async def test_final_storage_failure_does_not_ack():
    store = MemoryStore()
    original_save = store.save

    async def save(state):
        if state.status == "succeeded":
            raise ConnectionError
        await original_save(state)

    store.save = save
    message = Delivery(JobMessage(job_id=uuid4(), prompt="x"))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"result": "ok"}))
    ) as client:
        with pytest.raises(ConnectionError):
            await process_delivery(message, store, client, Settings(_env_file=None), Metrics())
    message.ack.assert_not_awaited()


async def test_malformed_message_rejected_without_requeue():
    message = Delivery(JobMessage(job_id=uuid4(), prompt="x"))
    message.body = b"not json"
    await process_delivery(message, MemoryStore(), None, Settings(_env_file=None), Metrics())
    message.reject.assert_awaited_once_with(requeue=False)
    message.ack.assert_not_awaited()


async def test_cancellation_does_not_ack():
    message = Delivery(JobMessage(job_id=uuid4(), prompt="x"))
    store = MemoryStore()
    started = asyncio.Event()

    async def responder(request):
        started.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
        task = asyncio.create_task(
            process_delivery(message, store, client, Settings(_env_file=None), Metrics())
        )
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    message.ack.assert_not_awaited()


async def test_idle_worker_stop_wakes_consumer():
    worker = Worker(Settings(_env_file=None), MemoryStore(), Metrics())

    class Iterator:
        async def __anext__(self):
            await asyncio.Event().wait()

    task = asyncio.create_task(worker.next_or_stop(Iterator()))
    worker.stop_event.set()
    assert await asyncio.wait_for(task, 1) is None


def test_logs_do_not_serialize_sensitive_extra_or_exception():
    record = logging.LogRecord("takehome", logging.INFO, "", 1, "job_finished", (), None)
    record.prompt = "private prompt"
    record.password = "private password"
    record.job_id = "abc"
    value = JsonFormatter().format(record)
    assert "private" not in value
    assert json.loads(value)["job_id"] == "abc"


def test_settings_hide_credentials():
    settings = Settings(_env_file=None, rabbitmq_url="amqp://user:secret@localhost/")
    assert "secret" not in repr(settings)


def test_kubernetes_service_links_do_not_override_listener_ports(monkeypatch):
    monkeypatch.setenv("API_PORT", "tcp://10.43.1.2:8000")
    monkeypatch.setenv("MOCK_PORT", "tcp://10.43.1.3:8081")
    monkeypatch.setenv("APP_WORKER_PORT", "9001")
    settings = Settings(_env_file=None)
    assert settings.app_api_port == 8000
    assert settings.app_mock_port == 8081
    assert settings.app_worker_port == 9001


async def test_publisher_reconnects_on_remote_close_callback(monkeypatch):
    from takehome.broker import Publisher

    class Session:
        def __init__(self):
            self.close_callbacks = set()
            self.is_closed = False
            self.connected = asyncio.Event()
            self.connected.set()

        async def close(self):
            self.is_closed = True

    sessions = []
    first = asyncio.Event()
    second = asyncio.Event()

    async def connection(_):
        session = Session()
        channel = Session()
        sessions.append((session, channel))
        (first if len(sessions) == 1 else second).set()
        return session, channel, None

    monkeypatch.setattr("takehome.broker.connect", connection)
    publisher = Publisher(Settings(_env_file=None, reconnect_delay_seconds=0.001))
    task = asyncio.create_task(publisher.run())
    try:
        async with asyncio.timeout(1):
            await first.wait()
            session, channel = sessions[0]
            session.connected.clear()
            channel.is_closed = True
            for callback in session.close_callbacks:
                callback(session, ConnectionError())
            await second.wait()
        assert publisher.ready
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
