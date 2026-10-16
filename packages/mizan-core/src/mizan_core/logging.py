"""Structured logging.

All output (our own loggers, uvicorn, third-party libraries) goes through one structlog
pipeline, so every line has the same shape and carries the active OpenTelemetry
``trace_id`` / ``span_id``. That is what lets us jump from a log line to its trace.
"""

from __future__ import annotations

import io
import logging
import sys
from collections.abc import Iterable
from typing import TextIO

import structlog
from opentelemetry import trace
from structlog.typing import EventDict, Processor, WrappedLogger


def add_trace_context(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


class DropAccessLogForPaths(logging.Filter):
    """Drop uvicorn access-log lines for noisy endpoints such as liveness probes."""

    def __init__(self, paths: Iterable[str]) -> None:
        super().__init__()
        self.paths = frozenset(paths)

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn.access args: (client_addr, method, path_with_query, http_version, status)
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            return args[2].partition("?")[0] not in self.paths
        return True


def configure_logging(
    level: str = "INFO",
    *,
    json_format: bool = False,
    quiet_paths: Iterable[str] = (),
    stream: TextIO | None = None,
) -> None:
    """Route all logging through structlog.

    Services log to stdout (twelve-factor); CLIs pass ``sys.stderr`` so that stdout carries
    only their result and stays pipeable.
    """
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        add_trace_context,
        structlog.processors.StackInfoRenderer(),
    ]

    renderer: Processor
    if json_format:
        renderer = structlog.processors.JSONRenderer()
        shared.append(structlog.processors.dict_tracebacks)
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    stream = stream or sys.stdout
    # Log lines carry Azerbaijani text. A Windows console or redirected file defaults to
    # cp1252, where "ə" cannot be encoded and the line - often a traceback - is lost.
    if isinstance(stream, io.TextIOWrapper) and stream.encoding.lower() not in {"utf-8", "utf8"}:
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # Let uvicorn propagate to the root handler instead of using its own formatters.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    access_logger = logging.getLogger("uvicorn.access")
    # Replace (not stack) our filter, so calling configure_logging twice is idempotent.
    access_logger.filters = [
        f for f in access_logger.filters if not isinstance(f, DropAccessLogForPaths)
    ]
    if quiet_paths:
        access_logger.addFilter(DropAccessLogForPaths(quiet_paths))


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger
