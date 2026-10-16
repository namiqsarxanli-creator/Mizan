from collections.abc import Iterator, Sequence
from datetime import date

import pytest
from fakes import FakeSearcher, fake_legal_services
from fastapi.testclient import TestClient

from mizan_api.main import create_app
from mizan_api.routes.legal import today_in_baku
from mizan_core.config import Environment, Settings
from mizan_retrieval.answer import Answer, AnswerUnavailableError
from mizan_retrieval.search import Hit


def client_for(searcher: FakeSearcher, *, with_llm: bool) -> TestClient:
    settings = Settings(_env_file=None, environment=Environment.TEST)
    services = fake_legal_services(searcher, with_llm=with_llm)
    return TestClient(create_app(settings, checks=[], legal=services))


@pytest.fixture
def searcher() -> FakeSearcher:
    return FakeSearcher()


@pytest.fixture
def client(searcher: FakeSearcher) -> Iterator[TestClient]:
    with client_for(searcher, with_llm=True) as c:
        yield c


def test_search_returns_sources_with_titles(client: TestClient, searcher: FakeSearcher) -> None:
    response = client.get("/v1/search", params={"q": "işdən çıxmaq", "as_of": "2020-01-01", "k": 3})

    assert response.status_code == 200
    body = response.json()
    assert body["as_of"] == "2020-01-01"
    assert body["results"][0]["title"] == "Maddə 69. Xitam"
    assert body["results"][0]["in_force_since"] == "2025-06-13"
    assert searcher.calls == [
        {"query": "işdən çıxmaq", "as_of": date(2020, 1, 1), "k": 3, "source_id": 46943}
    ]


def test_search_defaults_to_today_in_baku(client: TestClient, searcher: FakeSearcher) -> None:
    client.get("/v1/search", params={"q": "məzuniyyət"})
    assert searcher.calls[0]["as_of"] == today_in_baku()


@pytest.mark.parametrize(
    "params", [{"q": "x"}, {"q": "ok", "k": 0}, {"q": "ok", "as_of": "2020-13-01"}]
)
def test_search_validates_input(client: TestClient, params: dict[str, str | int]) -> None:
    assert client.get("/v1/search", params=params).status_code == 422


def test_ask_returns_answer_with_citations(client: TestClient) -> None:
    response = client.post("/v1/ask", json={"question": "Neçə gün əvvəl xəbər verməliyəm?"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"].startswith("İşəgötürəni bir ay")
    assert body["citations"] == [
        {"key": "art:69", "title": "Maddə 69. Xitam", "quote": "bir təqvim ayı qabaqcadan"}
    ]
    assert body["refused"] is False
    assert body["rejected_citations"] == 1  # the invented quote was dropped
    assert body["disclaimer"]


def test_ask_rewrites_the_query_and_answers_from_whole_articles(
    client: TestClient, searcher: FakeSearcher
) -> None:
    body = client.post("/v1/ask", json={"question": "Məni qova bilərlər?"}).json()

    assert searcher.calls[-1]["query"] == "Məni qova bilərlər?\nhüquqi ifadə"
    assert [s["key"] for s in body["sources"]] == ["art:69"]  # part hit expanded to its article


def test_search_does_not_call_the_llm(client: TestClient, searcher: FakeSearcher) -> None:
    client.get("/v1/search", params={"q": "Məni qova bilərlər?"})
    assert searcher.calls[-1]["query"] == "Məni qova bilərlər?"


def test_ask_without_llm_is_503_but_search_still_works(searcher: FakeSearcher) -> None:
    with client_for(searcher, with_llm=False) as c:
        ask = c.post("/v1/ask", json={"question": "Sual?"})
        search = c.get("/v1/search", params={"q": "sual"})

    assert ask.status_code == 503
    assert "MIZAN_LLM__GROQ_API_KEY" in ask.json()["detail"]
    assert search.status_code == 200


def test_provider_outage_is_a_503_with_retry_after(searcher: FakeSearcher) -> None:
    class RateLimited:
        async def answer(self, question: str, hits: Sequence[Hit], *, as_of: date) -> Answer:
            raise AnswerUnavailableError("rate-limited", retry_after_s=7)

    settings = Settings(_env_file=None, environment=Environment.TEST)
    services = fake_legal_services(searcher, with_llm=True)
    services.answerer = RateLimited()
    with TestClient(create_app(settings, checks=[], legal=services)) as c:
        response = c.post("/v1/ask", json={"question": "Sual?"})

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "7"
    assert "/v1/search still works" in response.json()["detail"]
