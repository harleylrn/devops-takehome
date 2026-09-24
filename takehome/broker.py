import asyncio

import aio_pika

from takehome.config import Settings
from takehome.logging import event
from takehome.models import JobMessage


async def connect(settings: Settings):
    connection = await aio_pika.connect(
        settings.rabbitmq_url.get_secret_value(),
        timeout=settings.dependency_timeout_seconds,
        heartbeat=10,
    )
    try:
        channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
        await channel.set_qos(prefetch_count=settings.worker_prefetch)
        queue = await channel.declare_queue(settings.queue_name, durable=True)
        return connection, channel, queue
    except BaseException:
        await connection.close()
        raise


class Publisher:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.connection = None
        self.channel = None
        self.stopping = False

    @property
    def ready(self):
        return bool(
            self.channel is not None
            and not self.channel.is_closed
            and self.connection is not None
            and not self.connection.is_closed
            and self.connection.connected.is_set()
        )

    async def run(self):
        while not self.stopping:
            try:
                self.connection, self.channel, _ = await connect(self.settings)
                disconnected = asyncio.Event()

                def on_closed(*_, target=disconnected):
                    target.set()

                # Connection.closed() tracks explicit close(), not every remote
                # transport loss. Channel/connection callbacks cover both cases.
                self.connection.close_callbacks.add(on_closed)
                self.channel.close_callbacks.add(on_closed)
                event("publisher_connected", service="api")
                if self.ready:
                    await disconnected.wait()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                event("publisher_disconnected", error_type=type(exc).__name__, service="api")
            finally:
                self.channel = None
                if self.connection is not None:
                    await self.connection.close()
                    self.connection = None
            await asyncio.sleep(self.settings.reconnect_delay_seconds)

    async def publish(self, job: JobMessage):
        if not self.ready:
            raise ConnectionError("publisher unavailable")
        await self.channel.default_exchange.publish(
            aio_pika.Message(
                body=job.model_dump_json().encode(),
                content_type="application/json",
                message_id=str(job.job_id),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=self.settings.queue_name,
            mandatory=True,
            timeout=self.settings.dependency_timeout_seconds,
        )

    async def close(self):
        self.stopping = True
        if self.connection is not None:
            await self.connection.close()
