"""Runs the real dependency checks against the local Docker stack.

docker compose up -d
MIZAN_INTEGRATION=1 uv run pytest -m integration
"""

import os
from contextlib import AsyncExitStack

import pytest

from mizan_api.dependencies import build_dependency_checks
from mizan_core.config import Settings
from mizan_core.health import run_checks

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("MIZAN_INTEGRATION") != "1",
        reason="set MIZAN_INTEGRATION=1 with the compose stack running",
    ),
]


async def test_every_backing_service_is_reachable() -> None:
    async with AsyncExitStack() as stack:
        checks = await build_dependency_checks(Settings(), stack)
        results = await run_checks(checks, timeout_s=5)

    assert set(results) == {"postgres", "redis", "qdrant", "neo4j"}
    failed = {name: r.error for name, r in results.items() if not r.ok}
    assert not failed
