"""/v1/ask with the semantic cache on: the second asking is served without an LLM call."""

from dataclasses import replace
from typing import Any

import numpy as np
from fakes import FakeGroq, fake_legal_services
from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient

from mizan_api.main import create_app
from mizan_core.config import Environment, Settings
from mizan_retrieval.answer import GroqAnswerer
from mizan_retrieval.cache import SemanticCache


class CountingGroq(FakeGroq):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def create(self, **request: Any) -> Any:
        self.calls += 1
        return await super().create(**request)


def embed(question: str) -> np.ndarray:
    # Questions about notice periods land together; anything else is orthogonal.
    return np.array([1.0, 0.0] if "xəbərdar" in question else [0.0, 1.0], dtype=np.float32)


def test_a_repeated_question_is_answered_from_the_cache() -> None:
    llm = CountingGroq()
    cache = SemanticCache(
        AsyncQdrantClient(":memory:"), dim=2, threshold=0.9, ttl_s=3600, version="t", embed=embed
    )
    services = replace(
        fake_legal_services(with_llm=True), answerer=GroqAnswerer(llm), rewriter=None, cache=cache
    )
    settings = Settings(_env_file=None, environment=Environment.TEST)
    with TestClient(create_app(settings, checks=[], legal=services)) as client:
        first = client.post("/v1/ask", json={"question": "İşəgötürəni nə vaxt xəbərdar edim?"})
        again = client.post("/v1/ask", json={"question": "Nə vaxt xəbərdar etməliyəm?"})
        other = client.post("/v1/ask", json={"question": "Məzuniyyət neçə gündür?"})

    assert first.status_code == again.status_code == other.status_code == 200
    assert first.json()["cached"] is False
    assert again.json()["cached"] is True
    assert again.json()["answer"] == first.json()["answer"]
    assert again.json()["citations"] == first.json()["citations"]
    assert other.json()["cached"] is False
    assert llm.calls == 2  # the first and the unrelated question
