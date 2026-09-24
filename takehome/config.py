from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    rabbitmq_url: SecretStr = SecretStr("amqp://localhost:5672/")
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    queue_name: str = Field(default="jobs", pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    external_service_url: str = "http://localhost:8081/infer"
    dependency_timeout_seconds: float = Field(default=3, gt=0, le=30)
    external_timeout_seconds: float = Field(default=5, gt=0, le=60)
    external_max_attempts: int = Field(default=3, ge=1, le=10)
    retry_backoff_seconds: float = Field(default=0.5, gt=0, le=10)
    reconnect_delay_seconds: float = Field(default=2, gt=0, le=30)
    worker_prefetch: int = Field(default=1, ge=1, le=32)
    shutdown_grace_seconds: float = Field(default=20, gt=0, le=120)
    result_ttl_seconds: int = Field(default=86400, ge=60)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    host: str = "0.0.0.0"
    # Prefix listener settings to avoid Kubernetes service-link variables such
    # as API_PORT=tcp://10.43.0.1:8000 or MOCK_PORT=tcp://10.43.0.2:8081.
    app_api_port: int = Field(default=8000, ge=1, le=65535)
    app_worker_port: int = Field(default=8001, ge=1, le=65535)
    app_mock_port: int = Field(default=8081, ge=1, le=65535)
    mock_mode: Literal["normal", "slow", "unavailable"] = "normal"
    mock_delay_seconds: float = Field(default=8, ge=0, le=60)
