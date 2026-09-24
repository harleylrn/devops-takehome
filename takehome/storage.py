from uuid import UUID

from redis.asyncio import Redis

from takehome.config import Settings
from takehome.models import JobState


class JobStore:
    def __init__(self, settings: Settings):
        self.client = Redis.from_url(
            settings.redis_url.get_secret_value(),
            decode_responses=True,
            socket_connect_timeout=settings.dependency_timeout_seconds,
            socket_timeout=settings.dependency_timeout_seconds,
        )
        self.ttl = settings.result_ttl_seconds
        self.prefix = f"takehome:{settings.queue_name}:"

    async def ping(self):
        return bool(await self.client.ping())

    async def get(self, job_id: UUID):
        value = await self.client.get(f"{self.prefix}{job_id}")
        return JobState.model_validate_json(value) if value else None

    async def save(self, state: JobState):
        await self.client.set(
            f"{self.prefix}{state.job_id}",
            state.model_dump_json(),
            ex=self.ttl,
        )

    async def close(self):
        await self.client.aclose()
