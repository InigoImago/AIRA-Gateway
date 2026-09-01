"""The export is narrated, and the narration does not go out by the thing it narrates (`FRD-617`).

Three properties, and each of them was a hole:

**An export attempt says what it did.** The SDK hands a batch to an exporter on a background
thread and keeps the answer to itself — a success is reported nowhere at any level, and the
failure is reported somewhere nobody could read (below). So *"did anything actually get through"*
had no answer inside the process.

**The SDK's own explanation reaches a terminal.** With `AIRA_OTEL_ENABLED=true` the only handler
on the root logger is the OTLP one, so the sentence saying why an export failed was queued for
export through the exporter that had just failed. `logging.lastResort` does not save it: it prints
only when a record finds *no* handler, and this one found the broken one. That is the mechanism
behind a whole day of not being able to see why OTLP pushes were failing.

**And a line about `otel` must not enter the OTLP log pipeline**, or reporting a failed log export
produces a log record, which is queued for export, which fails, which produces another line.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    MetricExportResult,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExportResult

from aira_common.integration_debug import configure_integration_debug
from aira_common.logging import configure_logging, get_logger
from aira_common.observability import (
    SdkDiagnostics,
    route_sdk_diagnostics,
    watched_export,
)


@pytest.fixture(autouse=True)
def _channel() -> Iterator[None]:
    configure_logging("INFO", json_output=True)
    configure_integration_debug("otel")
    yield
    configure_integration_debug("")


def calls(capsys: Any) -> list[dict]:
    return [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip().startswith("{") and '"integration_call"' in line
    ]


class FakeSpanExporter:
    """Stands in for `OTLPSpanExporter`: it answers with a *value*, never by raising."""

    def __init__(self, result: SpanExportResult = SpanExportResult.SUCCESS) -> None:
        self.result = result
        self.batches: list[int] = []

    def export(self, spans: Any) -> SpanExportResult:
        self.batches.append(len(spans))
        return self.result

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


def _one_span(exporter: Any) -> None:
    """Drive a **real** `BatchSpanProcessor`, so the wrapper is exercised the way the SDK uses it.

    Asserting that `export` was called directly would pass against a wrapper the SDK refuses to
    accept — which is the failure mode a duck-typed stand-in has (`LESSONS.md` §1: test the wire).
    """
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    provider.get_tracer("t").start_span("work").end()
    provider.force_flush()
    provider.shutdown()


def test_a_successful_export_says_so_with_a_count_and_a_duration(capsys: Any) -> None:
    _one_span(watched_export(FakeSpanExporter(), "traces", "http://collector:4318/v1/traces"))

    (line,) = [c for c in calls(capsys) if c["operation"] == "export"]
    assert line["outcome"] == "ok"
    assert line["signal"] == "traces"
    assert line["target"] == "http://collector:4318/v1/traces"
    assert line["items"] == 1
    assert line["result"] == "SUCCESS"
    assert line["duration_ms"] >= 0


def test_an_exporter_that_answers_failure_is_not_reported_as_a_slow_success(capsys: Any) -> None:
    """The failure that raises nothing. An OTLP exporter returns `FAILURE` after it has exhausted
    its own retries — a channel watching only for exceptions would call that a success."""
    _one_span(
        watched_export(FakeSpanExporter(SpanExportResult.FAILURE), "traces", "http://nowhere:4318")
    )

    (line,) = [c for c in calls(capsys) if c["operation"] == "export"]
    assert line["outcome"] == "failed"
    assert line["result"] == "FAILURE"
    assert line["level"] == "warning"


def test_an_exporter_that_raises_is_reported_and_the_exception_still_reaches_the_sdk(
    capsys: Any,
) -> None:
    class Broken(FakeSpanExporter):
        def export(self, spans: Any) -> SpanExportResult:
            raise ConnectionRefusedError("[Errno 111] Connection refused")

    _one_span(watched_export(Broken(), "traces", "http://collector:4318/v1/traces"))

    (line,) = [c for c in calls(capsys) if c["operation"] == "export"]
    assert line["outcome"] == "failed"
    assert line["error_type"] == "ConnectionRefusedError"
    assert "Connection refused" in line["error"]


class FakeMetricExporter:
    """`PeriodicExportingMetricReader` reaches past the interface for these two attributes.

    That is why the wrapper forwards by `__getattr__` rather than implementing a protocol: a
    wrapper that satisfied only the declared surface would fail at construction, here, and nowhere
    a unit test of `export` would look.
    """

    _preferred_temporality: dict = {}  # noqa: RUF012
    _preferred_aggregation: dict = {}  # noqa: RUF012

    def export(self, metrics_data: Any, timeout_millis: float = 10_000, **kwargs: Any) -> Any:
        return MetricExportResult.SUCCESS

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        return None

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return True


def test_the_metric_reader_accepts_the_wrapper_and_its_export_is_reported(capsys: Any) -> None:
    reader = PeriodicExportingMetricReader(
        watched_export(FakeMetricExporter(), "metrics", "http://collector:4318/v1/metrics"),
        export_interval_millis=60_000,
    )
    provider = MeterProvider(metric_readers=[reader])
    provider.get_meter("t").create_counter("requests").add(1)
    provider.force_flush()
    provider.shutdown()

    # `force_flush` and `shutdown` each drive a collection cycle; the first is the one that
    # carried the counter.
    line = next(c for c in calls(capsys) if c["operation"] == "export")
    assert line["signal"] == "metrics"
    assert line["outcome"] == "ok"
    # Metrics arrive as a tree, not a sequence: "we did not count" is `None`, never `0`.
    assert line["items"] is None


def test_the_wrapper_forwards_everything_it_does_not_define() -> None:
    exporter = FakeSpanExporter()
    wrapped = watched_export(exporter, "traces", "http://x")
    assert wrapped.force_flush() is True
    assert wrapped.batches is exporter.batches
    with pytest.raises(AttributeError):
        wrapped.no_such_thing  # noqa: B018


# --- the SDK's own words ------------------------------------------------------------------------


@pytest.fixture
def exported() -> Iterator[InMemoryLogRecordExporter]:
    """A log pipeline attached where the real OTLP one is attached: the **root** logger."""
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    handler = LoggingHandler(logger_provider=provider)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield exporter
    finally:
        root.removeHandler(handler)
        provider.shutdown()


@pytest.fixture(autouse=True)
def _restore_sdk_logger() -> Iterator[None]:
    """Start from an un-routed SDK logger and put back whatever was there.

    `route_sdk_diagnostics` is idempotent and process-wide, so any earlier test that built an
    observability stack has already installed it — and these cases are about the installation
    itself. Cleared at setup rather than only at teardown, because a test whose verdict depends on
    which files ran before it is one that passes alone and fails in the suite.
    """
    logger = logging.getLogger("opentelemetry")
    handlers, propagate = logger.handlers[:], logger.propagate
    logger.handlers = [h for h in logger.handlers if not isinstance(h, SdkDiagnostics)]
    yield
    logger.handlers, logger.propagate = handlers, propagate


def _bodies(exporter: InMemoryLogRecordExporter) -> list[str]:
    return [str(record.log_record.body) for record in exporter.get_finished_logs()]


def test_the_sdks_explanation_is_printed_instead_of_posted_to_itself(
    exported: InMemoryLogRecordExporter, capsys: Any
) -> None:
    assert route_sdk_diagnostics() is True
    logging.getLogger("opentelemetry.exporter.otlp.proto.http").error(
        "Failed to export traces to collector:4318, error code: StatusCode.UNAVAILABLE"
    )

    printed = capsys.readouterr().out
    assert "StatusCode.UNAVAILABLE" in printed, (
        "the SDK's own explanation of a failed export must reach a terminal; before FRD-617 the "
        "only handler on the root logger was the OTLP one, so it was posted to the exporter that "
        "had just failed"
    )
    assert not any("UNAVAILABLE" in body for body in _bodies(exported))


def test_routing_the_sdk_logger_is_idempotent() -> None:
    route_sdk_diagnostics()
    assert route_sdk_diagnostics() is False
    logger = logging.getLogger("opentelemetry")
    assert sum(isinstance(h, SdkDiagnostics) for h in logger.handlers) == 1
    assert logger.propagate is False


def test_a_record_the_relay_cannot_render_does_not_raise() -> None:
    """A diagnostic must never be the reason something fails — least of all inside logging.

    The handler is called directly rather than through `logging.getLogger(...).error(...)`: pytest
    attaches a capture handler of its own that re-raises formatting errors during a test, so the
    chain version fails on **pytest's** handler and says nothing about ours.
    """
    record = logging.LogRecord(
        "opentelemetry.exporter", logging.ERROR, __file__, 1, "bad format %d", ("nope",), None
    )
    SdkDiagnostics().emit(record)  # must not raise


# --- the loop ------------------------------------------------------------------------------------


def test_a_line_about_otel_reaches_stdout_and_not_the_export_pipeline(
    exported: InMemoryLogRecordExporter, capsys: Any
) -> None:
    """`FRD-617` §3.2. The one property that keeps this feature from feeding itself."""
    get_logger("t").warning("otel_sdk", message="export failed", local_only=True)

    printed = capsys.readouterr().out
    assert "export failed" in printed
    # And the flag itself is off the rendered line: it explains a mechanism the reader of a log
    # does not need.
    assert "local_only" not in printed
    assert not any("export failed" in body for body in _bodies(exported))


def test_an_ordinary_line_still_reaches_the_export_pipeline(
    exported: InMemoryLogRecordExporter,
) -> None:
    """The hold-back must not leak into the next line. It is set on **every** line, so a
    context variable left standing from a previous call cannot silence an unrelated one."""
    get_logger("t").warning("otel_sdk", message="held back", local_only=True)
    get_logger("t").warning("served", model="mock-1")

    bodies = _bodies(exported)
    assert any("served" in body for body in bodies)
    assert not any("held back" in body for body in bodies)


def test_an_export_line_for_otel_is_held_back_but_one_for_kafka_is_not(
    exported: InMemoryLogRecordExporter,
) -> None:
    """The channel marks only `otel`, because only `otel` is circular. A `kafka` line belongs in
    the trace backend like any other."""
    from aira_common.integration_debug import report

    configure_integration_debug("otel,kafka")
    report("otel", "export", signal="traces")
    report("kafka", "producer.send", topic="aira.usecases")

    bodies = _bodies(exported)
    assert any("aira.usecases" in body for body in bodies)
    assert not any('"signal": "traces"' in body for body in bodies)
