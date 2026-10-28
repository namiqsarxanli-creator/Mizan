"""Article lookup and collision checks: the agent's tools over HTTP, without an LLM."""

from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import date

import pytest
from fakes import FakeSearcher, fake_legal_services
from fastapi.testclient import TestClient

from mizan_agents.tools import LegalTools
from mizan_api.main import create_app
from mizan_core.config import Environment, Settings
from mizan_graph.build import Reference
from mizan_graph.citations import SELF
from mizan_graph.decisions import Decision
from mizan_graph.store import InMemoryGraph
from mizan_retrieval.corpus import OPEN_END
from mizan_retrieval.search import Hit, SearchConfig

TEXTS = {
    "art:51": "Kod > Maddə 51. Sınaq müddəti\n52-ci maddədə göstərilən hallar istisna olmaqla...",
    "art:52": "Kod > Maddə 52. Sınaq müddəti müəyyən edilməyən hallar\nYaşı 18-dən az olan...",
    "art:144": "Kod > Maddə 144. Kompensasiya\nƏlavə məzuniyyətlərə görə kompensasiya verilmir.",
}
DECISION = Decision(
    61412,
    "Əmək Məcəlləsinin 144-cü maddəsinin ... uyğunluğunun yoxlanılmasına dair",
    date(2026, 2, 18),
    "unconstitutional",
    "1. ... tətbiqi dayandırılsın.",
    ("art:144",),
)
GRAPH = InMemoryGraph(
    46943,
    [Reference("art:51", SELF, "art:52", ("52",), date(1999, 2, 1), OPEN_END, exception=True)],
    [DECISION],
)


async def expand(hits: Sequence[Hit], source_id: int, as_of: date) -> list[Hit]:
    return [
        Hit(h.article_key, h.article_key, h.article_key, TEXTS[h.article_key], 0.0,
            date(2021, 5, 27), ())
        for h in hits
        if h.article_key in TEXTS
    ]  # fmt: skip


@pytest.fixture
def client() -> Iterator[TestClient]:
    tools = LegalTools(FakeSearcher(), SearchConfig(), expand, GRAPH, GRAPH)
    services = replace(fake_legal_services(), tools=tools)
    settings = Settings(_env_file=None, environment=Environment.TEST)
    with TestClient(create_app(settings, checks=[], legal=services)) as c:
        yield c


def test_article_comes_with_the_decisions_that_interpret_it(client: TestClient) -> None:
    response = client.get("/v1/articles/art:144", params={"as_of": "2026-10-13"})

    assert response.status_code == 200
    body = response.json()
    assert body["key"] == "art:144"
    assert body["title"] == "Maddə 144. Kompensasiya"
    assert body["in_force_since"] == "2021-05-27"
    assert [d["key"] for d in body["decisions"]] == ["kmq:61412"]
    assert body["decisions"][0]["outcome"] == "unconstitutional"


def test_a_decision_after_the_date_is_not_attached(client: TestClient) -> None:
    body = client.get("/v1/articles/art:144", params={"as_of": "2026-01-01"}).json()
    assert body["decisions"] == []


def test_unknown_article_is_404_and_a_malformed_key_is_422(client: TestClient) -> None:
    assert client.get("/v1/articles/art:999").status_code == 404
    assert client.get("/v1/articles/maddə-5").status_code == 422


def test_conflicts_resolve_an_exception_by_lex_specialis(client: TestClient) -> None:
    response = client.get(
        "/v1/conflicts", params={"keys": ["art:51", "art:52"], "as_of": "2026-10-13"}
    )

    assert response.status_code == 200
    [resolution] = response.json()["resolutions"]
    assert resolution["rule"] == "lex specialis"
    assert resolution["applies"] == "art:52"
    assert "10.4" in resolution["basis"]


def test_a_voided_provision_loses_to_the_constitution(client: TestClient) -> None:
    body = client.get("/v1/conflicts", params={"keys": ["art:144"], "as_of": "2026-10-13"}).json()
    assert [r["rule"] for r in body["resolutions"]] == ["constitutional court"]
    assert body["resolutions"][0]["applies"] == "Konstitusiya"


def test_conflicts_reject_non_article_keys(client: TestClient) -> None:
    response = client.get("/v1/conflicts", params={"keys": ["art:51", "DROP TABLE"]})
    assert response.status_code == 422


def test_without_the_graph_the_routes_are_503() -> None:
    settings = Settings(_env_file=None, environment=Environment.TEST)
    with TestClient(create_app(settings, checks=[], legal=fake_legal_services())) as c:
        assert c.get("/v1/articles/art:144").status_code == 503
        assert c.get("/v1/conflicts", params={"keys": ["art:51"]}).status_code == 503
