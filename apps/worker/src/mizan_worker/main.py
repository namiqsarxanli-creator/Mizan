"""Arq worker.

    arq mizan_worker.main.WorkerSettings

Jobs:
* ``sync_document(source_id)`` - on demand (enqueue from the API or a script);
* nightly cron at 03:00 UTC syncing every tracked document.

Arq gives us retries, a job timeout and at-most-one-instance-per-job-id; the pipeline itself is
idempotent, so a retried or duplicated job is harmless.
"""

from __future__ import annotations

from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings
from arq.typing import WorkerCoroutine

from mizan_core.config import get_settings
from mizan_core.logging import configure_logging, get_logger
from mizan_ingestion.db.session import create_engine, session_factory
from mizan_ingestion.pipeline import sync_document as run_sync
from mizan_ingestion.sources.eqanun import EQanunClient

log = get_logger(__name__)

Ctx = dict[str, Any]


async def startup(ctx: Ctx) -> None:
    settings = get_settings()
    configure_logging(settings.log.level, json_format=settings.log.json_format)
    ctx["engine"] = create_engine(settings.postgres)
    ctx["sessions"] = session_factory(ctx["engine"])
    ctx["client"] = EQanunClient(settings.ingestion)
    log.info("worker_started", tracked=settings.ingestion.tracked_documents)


async def shutdown(ctx: Ctx) -> None:
    await ctx["client"].__aexit__(None, None, None)
    await ctx["engine"].dispose()


async def sync_document(ctx: Ctx, source_id: int, force_reparse: bool = False) -> dict[str, Any]:
    report = await run_sync(ctx["client"], ctx["sessions"], source_id, force_reparse=force_reparse)
    return {
        "source_id": report.source_id,
        "outcomes": {k.value: v for k, v in report.outcomes.items()},
        "downloaded": report.downloaded,
        "warnings": len(report.warnings),
    }


async def sync_tracked(ctx: Ctx) -> None:
    # Enqueue one job per document instead of looping here: each gets its own retries and
    # timeout, and one failing document does not block the others.
    for source_id in get_settings().ingestion.tracked_documents:
        await ctx["redis"].enqueue_job("sync_document", source_id, _job_id=f"sync:{source_id}")


class WorkerSettings:
    functions: ClassVar[list[WorkerCoroutine]] = [sync_document]
    cron_jobs: ClassVar[list[Any]] = [cron(sync_tracked, hour=3, minute=0, run_at_startup=False)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis.url)
    job_timeout = 30 * 60
    max_tries = 3
    health_check_interval = 30
