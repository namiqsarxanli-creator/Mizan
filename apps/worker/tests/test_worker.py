from typing import Any

import pytest

from mizan_core.config import get_settings
from mizan_worker import main


class FakeRedis:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def enqueue_job(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.jobs.append((name, args, kwargs))


async def test_cron_enqueues_one_deduplicated_job_per_tracked_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings().model_copy(deep=True)
    settings.ingestion.tracked_documents = [46943, 1000]
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    redis = FakeRedis()

    await main.sync_tracked({"redis": redis})

    assert redis.jobs == [
        ("sync_document", (46943,), {"_job_id": "sync:46943"}),
        ("sync_document", (1000,), {"_job_id": "sync:1000"}),
    ]


def test_worker_settings() -> None:
    assert main.WorkerSettings.functions == [main.sync_document]
    assert main.WorkerSettings.max_tries == 3
    assert len(main.WorkerSettings.cron_jobs) == 1
