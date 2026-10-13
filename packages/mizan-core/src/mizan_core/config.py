"""Typed application settings.

Every value comes from environment variables prefixed with ``MIZAN_`` (or a local ``.env``
file). Nested sections use ``__`` as a delimiter, e.g. ``MIZAN_POSTGRES__HOST=db``.

Connection defaults point at the host ports published by ``compose.yaml``, so code run on the
host talks to the local Docker stack without any configuration.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Self
from urllib.parse import quote

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Shared with compose.yaml / .env.example so a fresh checkout works out of the box.
# Refused outside local/test environments (see Settings._reject_dev_secrets).
DEV_PASSWORD = "mizan-dev-password"  # noqa: S105


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def is_deployed(self) -> bool:
        return self in {Environment.STAGING, Environment.PRODUCTION}


class LogSettings(BaseModel):
    level: str = "INFO"
    json_format: bool = False


class TelemetrySettings(BaseModel):
    otlp_endpoint: str | None = None
    sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)


class PostgresSettings(BaseModel):
    host: str = "localhost"
    port: int = 15432
    user: str = "mizan"
    password: SecretStr = SecretStr(DEV_PASSWORD)
    database: str = "mizan"

    @property
    def dsn(self) -> str:
        user = quote(self.user, safe="")
        password = quote(self.password.get_secret_value(), safe="")
        return f"postgresql://{user}:{password}@{self.host}:{self.port}/{self.database}"


class RedisSettings(BaseModel):
    url: str = "redis://localhost:16379/0"


class QdrantSettings(BaseModel):
    url: str = "http://localhost:16333"
    api_key: SecretStr | None = None


class Neo4jSettings(BaseModel):
    uri: str = "bolt://localhost:17687"
    user: str = "neo4j"
    password: SecretStr = SecretStr(DEV_PASSWORD)


class HealthSettings(BaseModel):
    check_timeout_s: float = Field(default=2.0, gt=0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MIZAN_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "mizan-api"
    environment: Environment = Environment.LOCAL

    log: LogSettings = LogSettings()
    telemetry: TelemetrySettings = TelemetrySettings()
    health: HealthSettings = HealthSettings()

    postgres: PostgresSettings = PostgresSettings()
    redis: RedisSettings = RedisSettings()
    qdrant: QdrantSettings = QdrantSettings()
    neo4j: Neo4jSettings = Neo4jSettings()

    @model_validator(mode="after")
    def _reject_dev_secrets(self) -> Self:
        if not self.environment.is_deployed:
            return self
        offenders = [
            name
            for name, secret in (
                ("postgres.password", self.postgres.password),
                ("neo4j.password", self.neo4j.password),
            )
            if secret.get_secret_value() == DEV_PASSWORD
        ]
        if offenders:
            raise ValueError(
                f"development secrets are not allowed in {self.environment}: {', '.join(offenders)}"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
