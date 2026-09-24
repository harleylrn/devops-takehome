import asyncio
import hashlib

from fastapi import FastAPI, HTTPException

from takehome.config import Settings
from takehome.models import JobMessage


def deterministic_result(prompt: str) -> str:
    return f"mock-result:{hashlib.sha256(prompt.encode()).hexdigest()[:16]}"


def create_app(settings=None):
    settings = settings or Settings()
    app = FastAPI(title="Mock inference service", version="1.0.0")

    @app.get("/livez")
    @app.get("/readyz")
    async def health():
        return {"status": "ready", "mode": settings.mock_mode}

    @app.post("/infer")
    async def infer(body: JobMessage):
        if settings.mock_mode == "unavailable":
            raise HTTPException(503, "mock dependency unavailable")
        if settings.mock_mode == "slow":
            await asyncio.sleep(settings.mock_delay_seconds)
        return {"result": deterministic_result(body.prompt)}

    return app
