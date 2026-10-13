"""Liveness and readiness probes.

* ``/health/live``  - the process is up. Never touches dependencies, so an outage in Postgres
  does not make the orchestrator restart healthy API pods.
* ``/health/ready`` - every dependency answers. Used to decide whether to route traffic here.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from mizan_core.health import CheckResult, run_checks

router = APIRouter(prefix="/health", tags=["health"])


class LivenessReport(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessReport(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, CheckResult]


@router.get("/live")
async def live() -> LivenessReport:
    return LivenessReport()


@router.get(
    "/ready",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessReport}},
)
async def ready(request: Request, response: Response) -> ReadinessReport:
    results = await run_checks(
        request.app.state.checks,
        timeout_s=request.app.state.settings.health.check_timeout_s,
    )
    is_ready = all(r.ok for r in results.values())
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessReport(status="ready" if is_ready else "not_ready", checks=results)
