import json
import logging

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from mizan_core.logging import DropAccessLogForPaths, configure_logging, get_logger


def access_record(path: str) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1", "GET", path, "1.1", 200),
        None,
    )


@pytest.mark.parametrize(
    ("path", "kept"),
    [("/health/live", False), ("/health/live?x=1", False), ("/health/ready", True)],
)
def test_access_log_filter(path: str, kept: bool) -> None:
    assert DropAccessLogForPaths(["/health/live"]).filter(access_record(path)) is kept


def test_json_logs_include_trace_context(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", json_format=True)
    tracer = TracerProvider().get_tracer("test")

    with tracer.start_as_current_span("unit") as span:
        get_logger("test").info("hello", answer=42)
        expected_trace_id = format(span.get_span_context().trace_id, "032x")

    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["event"] == "hello"
    assert line["answer"] == 42
    assert line["level"] == "info"
    assert line["trace_id"] == expected_trace_id
    assert trace.get_current_span().get_span_context().is_valid is False
