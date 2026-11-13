"""Typed application settings.

Every value comes from environment variables prefixed with ``MIZAN_`` (or a local ``.env``
file). Nested sections use ``__`` as a delimiter, e.g. ``MIZAN_POSTGRES__HOST=db``.

Connection defaults point at the host ports published by ``compose.yaml``, so code run on the
host talks to the local Docker stack without any configuration. They use ``127.0.0.1``, not
``localhost``: on Windows ``localhost`` resolves to ``::1`` first, Docker publishes on IPv4 only,
and every connection then waits ~2 s for the IPv6 attempt to fail.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal, Self
from urllib.parse import quote

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
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
    host: str = "127.0.0.1"
    port: int = 15432
    user: str = "mizan"
    password: SecretStr = SecretStr(DEV_PASSWORD)
    database: str = "mizan"

    @property
    def dsn(self) -> str:
        user = quote(self.user, safe="")
        password = quote(self.password.get_secret_value(), safe="")
        return f"postgresql://{user}:{password}@{self.host}:{self.port}/{self.database}"

    @property
    def sqlalchemy_url(self) -> str:
        return self.dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


class RedisSettings(BaseModel):
    url: str = "redis://127.0.0.1:16379/0"


class QdrantSettings(BaseModel):
    url: str = "http://127.0.0.1:16333"
    api_key: SecretStr | None = None

    _blank_key_is_unset = field_validator("api_key", mode="before")(
        lambda v: None if isinstance(v, str) and not v.strip() else v
    )


class Neo4jSettings(BaseModel):
    uri: str = "bolt://127.0.0.1:17687"
    user: str = "neo4j"
    password: SecretStr = SecretStr(DEV_PASSWORD)


class HealthSettings(BaseModel):
    check_timeout_s: float = Field(default=2.0, gt=0)


class IngestionSettings(BaseModel):
    raw_dir: str = "data/raw"
    eqanun_api_url: str = "https://api.e-qanun.az/"
    eqanun_site_url: str = "https://e-qanun.az/"
    # Identify ourselves honestly; e-qanun only requires a same-site Referer (hotlink guard).
    user_agent: str = "Mizan-research/0.1 (+https://github.com/namiqsarxanli-creator/Mizan)"
    min_request_interval_s: float = Field(default=1.5, ge=0)
    request_timeout_s: float = Field(default=60.0, gt=0)
    max_attempts: int = Field(default=4, ge=1)
    # e-qanun framework ids to keep in sync (46943 = Labour Code).
    tracked_documents: list[int] = [46943]


class RetrievalSettings(BaseModel):
    """Serving configuration; the defaults are the variant chosen by the F2 eval (ADR-0004)."""

    source_id: int = 46943
    # Dense-only won the F2 eval: fusing BM25 lowered MRR on colloquial questions (ADR-0004).
    mode: str = "dense"
    dense_model: str = "bge-m3"
    sparse: str = "bm25_p5"
    strategy: str = "article"
    candidates: int = Field(default=50, ge=1, le=500)
    rerank_top: int = Field(default=0, ge=0, le=100)
    # Articles named in the question ("69-cu maddə") are ranked first (F12: fixes q121, no
    # Hit@5 loss on v1 or on the held-out colloquial set).
    article_refs: bool = True
    embeddings_cache: str = "data/embeddings/cache.sqlite"
    # Graph-aware retrieval (ADR-0006): one top-5 place goes to the most similar article cited
    # by the best hit. +17% Recall@5 on multi-hop questions, no harm elsewhere (F4 eval).
    graph: bool = True
    # Answer with the Constitutional Court decisions interpreting the retrieved articles: the
    # consolidated text can lag behind them (Art. 144(3), suspended on 2026-02-18).
    court_decisions: bool = True
    # Load models at startup (readiness stays false until done) instead of on first request.
    warmup: bool = True
    # Embed concurrent queries together (QueryBatcher): ~2x search throughput on CPU (F11).
    batch_queries: bool = True
    batch_wait_ms: float = Field(default=10, ge=0, le=100)


class LLMSettings(BaseModel):
    """Answer generation. Disabled (``/v1/ask`` returns 503) until the chosen provider has a key."""

    provider: Literal["groq", "anthropic"] = "groq"
    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-120b"
    anthropic_api_key: SecretStr | None = None
    # Other OpenAI-compatible providers (mizan_core.llm_clients), e.g. for offline data work.
    gemini_api_key: SecretStr | None = None
    cerebras_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5-5"
    # Reasoning depth: Groq gpt-oss "reasoning_effort" / Claude "effort".
    effort: Literal["low", "medium", "high"] = "medium"
    # Restate questions in the Code's terminology before searching (Groq only; /v1/ask only).
    # Off by default: no significant retrieval gain in F2, +~1.5 s per /ask (ADR-0004);
    # confirmed on a held-out colloquial set in F12 (rewrite -0.033 Recall@5).
    rewrite_queries: bool = False
    rewrite_model: str = "openai/gpt-oss-20b"
    # Characters of source text sent per answer (Groq free tier: 8k tokens/minute in total).
    max_context_chars: int = Field(default=9000, ge=1000)
    # LLM tokens one tenant may spend per day (Baku time) across /v1/ask and the agent.
    daily_tokens_per_tenant: int = Field(default=300_000, ge=0)

    _blank_keys_are_unset = field_validator(
        "groq_api_key",
        "anthropic_api_key",
        "gemini_api_key",
        "cerebras_api_key",
        "openrouter_api_key",
        mode="before",
    )(lambda v: None if isinstance(v, str) and not v.strip() else v)


class QualitySettings(BaseModel):
    """Production quality loop (F6): answer log, online evaluation, feedback."""

    enabled: bool = True
    # Share of answers judged for faithfulness after the response is sent.
    online_eval_sample_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    judge_model: str = "qwen/qwen3.8-27b"
    # Human review queue (F9): agent answers below this confidence go to a person.
    review_below: float = Field(default=0.7, ge=0.0, le=1.0)
    unfaithful_below: float = Field(default=0.8, ge=0.0, le=1.0)
    # Bearer token for the reviewer endpoints (/v1/reviews); unset disables them. Until F10
    # adds user auth, this is the only thing between the queue and the internet.
    review_token: SecretStr | None = None


class CacheSettings(BaseModel):
    """Semantic answer cache for /v1/ask (F9). Off by default: it missed its saving gate (23.9%
    vs 25%) on the calibration workload and had one near-duplicate false hit; see
    evals/results/f9_cache."""

    enabled: bool = False
    # Calibrated (mizan-eval cache): the smallest cosine with <= 1% false hits on near-miss pairs.
    threshold: float = Field(default=0.9431, ge=0.0, le=1.0)
    ttl_hours: int = Field(default=168, ge=1)


class ShadowSettings(BaseModel):
    """Shadow deployment (F9): a candidate answers a sample of /v1/ask requests unseen."""

    enabled: bool = False
    model: str = "openai/gpt-oss-20b"  # the candidate under test
    sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)


class AuthSettings(BaseModel):
    """Authentication, rate limiting and the audit log for /v1 (F10).

    Off (local development): the tenant comes from ``X-Tenant-Id`` and every caller may do
    everything. On: every /v1 request needs ``Authorization: Bearer <JWT>`` signed with
    ``jwt_secret`` (HS256); the tenant and the scopes come from the token, never from headers.
    """

    enabled: bool = False
    jwt_secret: SecretStr | None = None
    issuer: str = "mizan"
    audience: str = "mizan-api"
    # Per subject, fixed one-minute windows in Redis; 0 turns the limit off.
    requests_per_minute: int = Field(default=60, ge=0)
    audit: bool = True


class DurableSettings(BaseModel):
    """Durable agent runs on Temporal (F9). Off: the API runs the agent in process."""

    enabled: bool = False
    address: str = "127.0.0.1:17233"
    namespace: str = "default"
    task_queue: str = "mizan-agent"


class ContractSettings(BaseModel):
    """Contract analysis (F10). The LLM review and OCR need the Groq key; rules always run."""

    llm_review: bool = True
    # None: the answering model (``llm.groq_model``).
    review_model: str | None = None
    batch_size: int = Field(default=6, ge=1, le=20)
    # Other providers serving the *same* review model, tried when Groq rate-limits
    # (``mizan_core.llm_clients.FailoverClient``). Providers without a key are skipped.
    fallback_models: list[str] = ["openrouter:openai/gpt-oss-120b"]
    ocr: bool = True
    ocr_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"


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

    ingestion: IngestionSettings = IngestionSettings()
    retrieval: RetrievalSettings = RetrievalSettings()
    llm: LLMSettings = LLMSettings()
    quality: QualitySettings = QualitySettings()
    cache: CacheSettings = CacheSettings()
    shadow: ShadowSettings = ShadowSettings()
    durable: DurableSettings = DurableSettings()
    auth: AuthSettings = AuthSettings()
    contracts: ContractSettings = ContractSettings()

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
        if not self.auth.enabled:
            raise ValueError(f"authentication must be enabled in {self.environment}")
        return self

    @model_validator(mode="after")
    def _auth_needs_a_strong_secret(self) -> Self:
        if self.auth.enabled:
            secret = self.auth.jwt_secret
            # HS256 keys shorter than the hash output are brute-forceable (RFC 7518 3.2).
            if secret is None or len(secret.get_secret_value()) < 32:
                raise ValueError("auth.jwt_secret must be at least 32 characters")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
