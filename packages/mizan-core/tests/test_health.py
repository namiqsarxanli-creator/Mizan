import asyncio
from dataclasses import dataclass

from mizan_core.health import run_checks


@dataclass
class FakeCheck:
    name: str
    delay_s: float = 0.0
    error: Exception | None = None

    async def check(self) -> None:
        await asyncio.sleep(self.delay_s)
        if self.error:
            raise self.error


async def test_all_healthy() -> None:
    results = await run_checks([FakeCheck("a"), FakeCheck("b")], timeout_s=1)

    assert all(r.ok for r in results.values())
    assert set(results) == {"a", "b"}


async def test_failure_exposes_only_exception_type() -> None:
    check = FakeCheck("db", error=ConnectionError("host 10.0.0.5 refused, password=hunter2"))

    result = (await run_checks([check], timeout_s=1))["db"]

    assert not result.ok
    assert result.error == "ConnectionError"


async def test_slow_check_times_out_without_blocking_others() -> None:
    checks = [FakeCheck("slow", delay_s=5), FakeCheck("fast")]

    results = await asyncio.wait_for(run_checks(checks, timeout_s=0.05), timeout=1)

    assert results["slow"].error == "Timeout"
    assert results["fast"].ok
