"""OpenTelemetry tracing setup.

Spans are always created (so every request has a trace id we can return to clients and put
in logs), but they are only exported when an OTLP endpoint is configured.
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased


def configure_tracing(
    *,
    service_name: str,
    service_version: str,
    environment: str,
    otlp_endpoint: str | None = None,
    sample_ratio: float = 1.0,
) -> TracerProvider:
    """Install a global SDK tracer provider (once per process) and return it.

    OpenTelemetry only allows the global provider to be set once; later calls (e.g. several
    apps built in one test session) reuse the existing provider.
    """
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        return current

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": service_name,
                "service.version": service_version,
                "deployment.environment.name": environment,
            }
        ),
        sampler=ParentBased(TraceIdRatioBased(sample_ratio)),
    )
    if otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    return provider


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
