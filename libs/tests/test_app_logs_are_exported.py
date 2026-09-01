"""An application log line reaches the OTLP handler, not only stdout (`FRD-001` FR-6).

`configure_observability` has attached an OTLP `LoggingHandler` to the **root** logger since
`FRD-001`, and `configure_logging` used `PrintLoggerFactory` — which writes to stdout and creates
no `logging.LogRecord` at all. So every line this system writes about itself went to stdout and
nowhere else, while the Compose stack collects no container output: traces and metrics arrived and
**logs were the signal nobody had**, under a diagram that says `logs (Loki)`.

Two correct halves and no wire (`LESSONS.md` §1), and a *documented* one — the 2026-08-31 DEVLOG
entry names it in passing as something "a reader will look for and not find", which is a note
rather than a fix. This file is the counterpart: it asks the **exporter**, because that is the end
the wire was missing, and asserting that a processor is in the chain would pass against a chain
that renders into nothing.
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

from aira_common.logging import configure_logging, get_logger


@pytest.fixture
def exported() -> Iterator[InMemoryLogRecordExporter]:
    """A log pipeline of this process's own, attached where the OTLP one is attached.

    The root logger, because that is where `configure_observability` puts the real handler — a
    test that attached it to `aira.app` itself would pass with `propagate` turned off, which is
    one of the three things that has to hold.
    """
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


def _bodies(exporter: InMemoryLogRecordExporter) -> list[str]:
    return [str(record.log_record.body) for record in exporter.get_finished_logs()]


def test_an_application_log_line_is_exported(exported: InMemoryLogRecordExporter) -> None:
    configure_logging("INFO", json_output=True)
    get_logger("aira_gateway.ratelimit").warning("rate_limited", use_case="uc-a", scope="member")

    bodies = [body for body in _bodies(exported) if "rate_limited" in body]
    assert bodies, (
        "the application's own log lines are not reaching the root logger, so the OTLP handler "
        "carries framework records only (FRD-001 FR-6)"
    )
    record = json.loads(bodies[0])
    assert record["event"] == "rate_limited"
    assert record["use_case"] == "uc-a"


def test_the_exported_line_keeps_its_severity(exported: InMemoryLogRecordExporter) -> None:
    """A severity that understates is how an alert stops firing: everything would arrive as
    `INFO` if the stdlib level were taken from a default rather than from the method called."""
    configure_logging("INFO", json_output=True)
    get_logger("t").error("config_event_failed", event_type="budget.upserted")

    severities = [
        record.log_record.severity_number
        for record in exported.get_finished_logs()
        if "config_event_failed" in str(record.log_record.body)
    ]
    assert severities and severities[0].name == "ERROR"


def test_stdout_still_carries_exactly_one_copy(
    exported: InMemoryLogRecordExporter, capsys: Any
) -> None:
    """One rendering, two sinks. A second copy on stdout would mean the line is rendered twice,
    and two renderings of one event are two answers to *what did this line say*."""
    configure_logging("INFO", json_output=True)
    get_logger("t").info("served", model="mock-1")

    out = capsys.readouterr().out
    assert out.count('"event": "served"') == 1
    assert json.loads(out.strip())["model"] == "mock-1"


def test_a_level_below_the_threshold_is_not_exported(
    exported: InMemoryLogRecordExporter,
) -> None:
    """The configured level still decides. Without a level of its own the export logger would
    inherit the root's `WARNING` and drop every `INFO` line; with none it would export what
    structlog was told to suppress."""
    configure_logging("WARNING", json_output=True)
    get_logger("t").info("suppressed")
    get_logger("t").warning("kept")

    bodies = _bodies(exported)
    assert not any("suppressed" in body for body in bodies)
    assert any("kept" in body for body in bodies)


def test_nothing_is_printed_a_second_time_when_no_handler_is_listening(capsys: Any) -> None:
    """With telemetry off there is no handler on the root logger, and `logging.lastResort` prints
    to **stderr** whenever a record finds none anywhere in the chain — so a gateway running
    without a collector would print every warning twice, once as JSON and once bare.

    The root handlers are taken away for the duration because pytest attaches its own, which would
    make this guard unable to fail.
    """
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers = []
    try:
        configure_logging("INFO", json_output=True)
        get_logger("t").warning("only-once")
        captured = capsys.readouterr()
    finally:
        root.handlers = saved

    assert captured.out.count("only-once") == 1
    assert "only-once" not in captured.err
