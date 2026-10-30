import pytest
from pydantic import SecretStr, ValidationError

from mizan_core.config import (
    DEV_PASSWORD,
    AuthSettings,
    Environment,
    Neo4jSettings,
    PostgresSettings,
    Settings,
    TelemetrySettings,
)


def test_defaults_work_for_local_development() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment is Environment.LOCAL
    assert settings.postgres.host == "127.0.0.1"  # not "localhost": see config docstring


def test_nested_settings_are_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIZAN_POSTGRES__HOST", "db.internal")
    monkeypatch.setenv("MIZAN_POSTGRES__PORT", "6543")
    monkeypatch.setenv("MIZAN_TELEMETRY__SAMPLE_RATIO", "0.25")

    settings = Settings(_env_file=None)

    assert settings.postgres.host == "db.internal"
    assert settings.postgres.port == 6543
    assert settings.telemetry.sample_ratio == 0.25


def test_dsn_escapes_special_characters_in_credentials() -> None:
    pg = PostgresSettings(
        user="mi@zan", password=SecretStr("p@ss:/word"), host="h", port=5432, database="d"
    )

    assert pg.dsn == "postgresql://mi%40zan:p%40ss%3A%2Fword@h:5432/d"


@pytest.mark.parametrize("environment", [Environment.STAGING, Environment.PRODUCTION])
def test_dev_secrets_are_rejected_when_deployed(environment: Environment) -> None:
    with pytest.raises(ValidationError, match=r"postgres\.password, neo4j\.password"):
        Settings(_env_file=None, environment=environment)


def test_real_secrets_are_accepted_in_production() -> None:
    settings = Settings(
        _env_file=None,
        environment=Environment.PRODUCTION,
        postgres=PostgresSettings(password=SecretStr("a-real-secret")),
        neo4j=Neo4jSettings(password=SecretStr("another-real-secret")),
        auth=AuthSettings(enabled=True, jwt_secret=SecretStr("x" * 32)),
    )

    assert settings.postgres.password.get_secret_value() != DEV_PASSWORD


def test_deployments_require_authentication() -> None:
    with pytest.raises(ValidationError, match="authentication must be enabled"):
        Settings(
            _env_file=None,
            environment=Environment.PRODUCTION,
            postgres=PostgresSettings(password=SecretStr("a-real-secret")),
            neo4j=Neo4jSettings(password=SecretStr("another-real-secret")),
        )


@pytest.mark.parametrize("secret", [None, "too-short"])
def test_auth_needs_a_strong_secret(secret: str | None) -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        Settings(
            _env_file=None,
            auth=AuthSettings(enabled=True, jwt_secret=SecretStr(secret) if secret else None),
        )


def test_sample_ratio_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, telemetry=TelemetrySettings(sample_ratio=1.5))


def test_blank_api_keys_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # `KEY=` in .env must disable the feature, not send an empty key to the provider.
    monkeypatch.setenv("MIZAN_LLM__GROQ_API_KEY", "")
    monkeypatch.setenv("MIZAN_QDRANT__API_KEY", "  ")

    settings = Settings(_env_file=None)

    assert settings.llm.groq_api_key is None
    assert settings.qdrant.api_key is None


def test_llm_provider_is_validated() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm={"provider": "openai"})  # type: ignore[arg-type]
