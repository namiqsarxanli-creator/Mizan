from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import pytest
from fakes import fake_legal_services
from fastapi.testclient import TestClient

from mizan_api.main import create_app
from mizan_api.middleware import TRACE_ID_HEADER
from mizan_core.config import Environment, Settings
from mizan_core.health import DependencyCheck


@dataclass
class StubCheck:
    name: str
    healthy: bool = True

    async def check(self) -> None:
        if not self.healthy:
            raise ConnectionError("unreachable")


def make_client(checks: Sequence[DependencyCheck]) -> TestClient:
    settings = Settings(_env_file=None, environment=Environment.TEST)
    return TestClient(create_app(settings, checks=checks, legal=fake_legal_services()))


@pytest.fixture
def healthy_client() -> Iterator[TestClient]:
    with make_client([StubCheck("postgres"), StubCheck("redis")]) as client:
        yield client


def test_liveness(healthy_client: TestClient) -> None:
    response = healthy_client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_when_all_dependencies_are_up(healthy_client: TestClient) -> None:
    response = healthy_client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert set(body["checks"]) == {"postgres", "redis"}


def test_readiness_returns_503_when_a_dependency_is_down() -> None:
    with make_client([StubCheck("postgres"), StubCheck("qdrant", healthy=False)]) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["qdrant"] == {
        "ok": False,
        "latency_ms": body["checks"]["qdrant"]["latency_ms"],
        "error": "ConnectionError",
    }
    assert body["checks"]["postgres"]["ok"] is True


def test_every_response_carries_a_trace_id(healthy_client: TestClient) -> None:
    first = healthy_client.get("/health/live").headers[TRACE_ID_HEADER]
    second = healthy_client.get("/health/live").headers[TRACE_ID_HEADER]

    assert len(first) == 32
    assert int(first, 16) != 0
    assert first != second


def test_incoming_trace_context_is_continued(healthy_client: TestClient) -> None:
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    traceparent = f"00-{trace_id}-00f067aa0ba902b7-01"

    response = healthy_client.get("/health/live", headers={"traceparent": traceparent})

    assert response.headers[TRACE_ID_HEADER] == trace_id
