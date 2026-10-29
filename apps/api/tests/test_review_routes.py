"""Reviewer endpoints: hidden unless configured, token-guarded, decisions validated."""

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

from fakes import fake_legal_services
from fastapi.testclient import TestClient
from pydantic import SecretStr

from mizan_api.main import create_app
from mizan_core.config import Environment, QualitySettings, Settings

ROW = {
    "id": 7,
    "answer_id": uuid.uuid4(),
    "reason": "low_confidence",
    "status": "open",
    "created_at": datetime(2026, 10, 29, tzinfo=UTC),
    "endpoint": "agent",
    "question": "Sınaq nə qədərdir?",
    "as_of": date(2026, 10, 29),
    "answer": "Üç ay.",
    "sources": ["art:51"],
    "confidence": 0.55,
    "reviewer": None,
    "corrected_answer": None,
    "note": None,
}


class FakeQueue:
    def __init__(self) -> None:
        self.decisions: list[dict[str, Any]] = []

    async def reviews(self, status: str = "open", limit: int = 50) -> list[dict[str, Any]]:
        return [ROW] if status == "open" else []

    async def resolve(self, review_id: int, **decision: Any) -> bool:
        self.decisions.append({"id": review_id, **decision})
        return review_id == 7

    async def drain(self) -> None:
        return None


def client(token: str | None, queue: FakeQueue | None) -> TestClient:
    quality = QualitySettings(review_token=SecretStr(token) if token else None)
    settings = Settings(_env_file=None, environment=Environment.TEST, quality=quality)
    services = replace(fake_legal_services(), quality=queue)  # type: ignore[arg-type]
    return TestClient(create_app(settings, checks=[], legal=services))


AUTH = {"Authorization": "Bearer s3cret"}


def test_without_a_token_configured_the_queue_does_not_exist() -> None:
    with client(None, FakeQueue()) as c:
        assert c.get("/v1/reviews", headers=AUTH).status_code == 404


def test_a_wrong_or_missing_token_is_401() -> None:
    with client("s3cret", FakeQueue()) as c:
        assert c.get("/v1/reviews").status_code == 401
        assert c.get("/v1/reviews", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_reviewer_lists_and_decides() -> None:
    queue = FakeQueue()
    with client("s3cret", queue) as c:
        listed = c.get("/v1/reviews", headers=AUTH)
        done = c.post(
            "/v1/reviews/7",
            headers=AUTH,
            json={
                "status": "corrected",
                "reviewer": "namiq",
                "corrected_answer": "Üç ay; 18 yaşa qədər yox.",
            },
        )
        unknown = c.post(
            "/v1/reviews/8", headers=AUTH, json={"status": "approved", "reviewer": "n"}
        )
        incomplete = c.post(
            "/v1/reviews/7", headers=AUTH, json={"status": "corrected", "reviewer": "n"}
        )

    assert listed.status_code == 200
    assert listed.json()[0]["reason"] == "low_confidence"
    assert done.status_code == 204
    assert queue.decisions[0]["corrected_answer"].startswith("Üç ay")
    assert unknown.status_code == 404
    assert incomplete.status_code == 422  # a correction must carry the corrected answer
