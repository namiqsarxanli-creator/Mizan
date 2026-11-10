"""Application factory.

Run with: ``uvicorn mizan_api.main:create_app --factory``
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from mizan_api import __version__
from mizan_api.auth import AuthMiddleware
from mizan_api.budget import BudgetExhaustedError
from mizan_api.dependencies import LegalServices, build_dependency_checks, build_legal_services
from mizan_api.middleware import TraceIdHeaderMiddleware
from mizan_api.routes import agent as agent_routes
from mizan_api.routes import contracts as contract_routes
from mizan_api.routes import feedback as feedback_routes
from mizan_api.routes import health
from mizan_api.routes import law as law_routes
from mizan_api.routes import legal as legal_routes
from mizan_api.routes import pages as page_routes
from mizan_api.routes import reviews as review_routes
from mizan_core.config import Settings, get_settings
from mizan_core.health import DependencyCheck
from mizan_core.logging import configure_logging, get_logger
from mizan_core.tracing import configure_tracing

log = get_logger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    checks: Sequence[DependencyCheck] | None = None,
    legal: LegalServices | None = None,
) -> FastAPI:
    """Build the API.

    ``checks`` and ``legal`` override the real dependency checks and search/answer services;
    tests use them to run without any backing services or models.
    """
    settings = settings or get_settings()
    configure_logging(
        settings.log.level,
        json_format=settings.log.json_format,
        quiet_paths=["/health/live"],
    )
    tracer_provider = configure_tracing(
        service_name=settings.service_name,
        service_version=__version__,
        environment=settings.environment,
        otlp_endpoint=settings.telemetry.otlp_endpoint,
        sample_ratio=settings.telemetry.sample_ratio,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            services = legal or await build_legal_services(settings, stack)
            app.state.searcher = services.searcher
            app.state.search_config = services.config
            app.state.answerer = services.answerer
            app.state.rewriter = services.rewriter
            app.state.expand = services.expand
            app.state.diff = services.diff
            app.state.agent = services.agent
            app.state.budget = services.budget
            app.state.quality = services.quality
            app.state.tools = services.tools
            app.state.cache = services.cache
            app.state.shadow = services.shadow
            app.state.rate_limiter = services.rate_limiter
            app.state.audit = services.audit
            app.state.contract_reviewer = services.contract_reviewer
            app.state.ocr = services.ocr
            app.state.checks = (
                list(checks)
                if checks is not None
                else [*await build_dependency_checks(settings, stack), *services.checks]
            )
            log.info("startup", environment=settings.environment, version=__version__)
            yield
            log.info("shutdown")
        tracer_provider.force_flush()

    app = FastAPI(title="Mizan API", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(AuthMiddleware, settings=settings.auth)
    app.add_middleware(TraceIdHeaderMiddleware)

    @app.exception_handler(BudgetExhaustedError)
    async def budget_exhausted(_: Request, exc: BudgetExhaustedError) -> JSONResponse:
        return JSONResponse(
            {"detail": "Daily token budget is spent; try again tomorrow."},
            status_code=429,
            headers={"Retry-After": str(exc.retry_after_s)},
        )

    app.include_router(health.router)
    app.include_router(legal_routes.router)
    app.include_router(law_routes.router)
    app.include_router(agent_routes.router)
    app.include_router(feedback_routes.router)
    app.include_router(review_routes.router)
    app.include_router(contract_routes.router)
    app.include_router(page_routes.router)

    # Wraps the whole middleware stack, so every request (including TraceIdHeaderMiddleware)
    # runs inside a server span.
    # Probes are traced too (their responses carry X-Trace-Id); the collector drops their spans.
    FastAPIInstrumentor.instrument_app(app, tracer_provider=tracer_provider)
    return app
