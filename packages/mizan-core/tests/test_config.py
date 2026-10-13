import pytest
from pydantic import SecretStr, ValidationError

from mizan_core.config import (
    DEV_PASSWORD,
    Environment,
    Neo4jSettings,
    PostgresSettings,
    Settings,
    TelemetrySettings,
)


def test_defaults_work_for_local_development() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment is Environment.LOCAL
    assert settings.postgres.host == "localhost"


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
    )

    assert settings.postgres.password.get_secret_value() != DEV_PASSWORD


def test_sample_ratio_is_bounded() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, telemetry=TelemetrySettings(sample_ratio=1.5))
