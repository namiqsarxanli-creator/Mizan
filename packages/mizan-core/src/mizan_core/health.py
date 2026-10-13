"""Dependency health checks shared by every Mizan service.

A service registers one ``DependencyCheck`` per backing system. ``run_checks`` executes them
concurrently with a per-check timeout, so one hung dependency cannot stall the probe.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel

from mizan_core.logging import get_logger

log = get_logger(__name__)


class DependencyCheck(Protocol):
    @property
    def name(self) -> str: ...

    async def check(self) -> None:
        """Return normally when healthy; raise on any failure."""
        ...


class CheckResult(BaseModel):
    ok: bool
    latency_ms: float
    # Only the exception type is exposed: probe responses may be publicly reachable, and
    # full error messages can leak hostnames or credentials. Details go to the logs.
    error: str | None = None


async def _run_one(check: DependencyCheck, timeout_s: float) -> CheckResult:
    start = time.perf_counter()
    error: str | None = None
    try:
        async with asyncio.timeout(timeout_s):
            await check.check()
    except TimeoutError:
        error = "Timeout"
        log.warning("health_check_timeout", dependency=check.name, timeout_s=timeout_s)
    except Exception as exc:  # noqa: BLE001 - a probe must report, never raise
        error = type(exc).__name__
        log.warning("health_check_failed", dependency=check.name, error=str(exc))
    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    return CheckResult(ok=error is None, latency_ms=latency_ms, error=error)


async def run_checks(
    checks: Sequence[DependencyCheck], *, timeout_s: float
) -> dict[str, CheckResult]:
    results = await asyncio.gather(*(_run_one(c, timeout_s) for c in checks))
    return {c.name: r for c, r in zip(checks, results, strict=True)}
