"""The lines a process writes before its export exists reach it, each at its own time
(`FRD-631` FR-1).

Vault's lines are written while the settings are read, and the export needs the settings: so
`vault_authenticated`, `vault_secrets_loaded` and the warning about a per-process pseudonym key
reached stdout and never the log store — the warnings that say an installation is misconfigured
were the ones no alert could see. They are held from the moment logging is configured and handed
over when the export is attached.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator

import pytest
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor

from aira_common.logging import EXPORT_LOGGER, configure_logging, get_logger
from aira_common.observability import early
from aira_common.observability.early import (
    EarlyRecords,
    discard_early_records,
    release_early_records,
)


@pytest.fixture(autouse=True)
def _undecided() -> Iterator[None]:
    early.reset_for_tests()
    yield
    early.reset_for_tests()


@pytest.fixture
def export() -> Iterator[tuple[LoggingHandler, InMemoryLogRecordExporter]]:
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    yield LoggingHandler(logger_provider=provider), exporter
    provider.shutdown()


def _bodies(exporter: InMemoryLogRecordExporter) -> list[dict[str, object]]:
    return [json.loads(str(r.log_record.body)) for r in exporter.get_finished_logs()]


def test_a_line_written_before_the_export_reaches_it_with_its_own_time(
    export: tuple[LoggingHandler, InMemoryLogRecordExporter],
) -> None:
    handler, exporter = export
    configure_logging("INFO")
    get_logger("aira_common.secrets").info("vault_secrets_loaded", count=3)
    written = time.time_ns()
    time.sleep(0.01)

    handed = release_early_records(handler)

    assert handed >= 1
    (line,) = [body for body in _bodies(exporter) if body["event"] == "vault_secrets_loaded"]
    (record,) = [
        r for r in exporter.get_finished_logs() if "vault_secrets_loaded" in str(r.log_record.body)
    ]
    assert record.log_record.timestamp is not None and record.log_record.timestamp <= written
    assert line["count"] == 3


def test_a_warning_written_before_the_export_keeps_its_severity(
    export: tuple[LoggingHandler, InMemoryLogRecordExporter],
) -> None:
    handler, exporter = export
    configure_logging("INFO")
    get_logger("t").warning("pseudonym_key_per_process", hint="set AIRA_PSEUDONYM_SECRET")

    release_early_records(handler)

    severities = [
        r.log_record.severity_number.name
        for r in exporter.get_finished_logs()
        if "pseudonym_key_per_process" in str(r.log_record.body)
    ]
    assert severities == ["WARN"]


def test_once_released_nothing_more_is_held(
    export: tuple[LoggingHandler, InMemoryLogRecordExporter],
) -> None:
    handler, _exporter = export
    configure_logging("INFO")
    release_early_records(handler)

    get_logger("t").info("after")

    assert not any(isinstance(h, EarlyRecords) for h in logging.getLogger(EXPORT_LOGGER).handlers)


def test_a_process_that_exports_no_logs_holds_nothing(
    export: tuple[LoggingHandler, InMemoryLogRecordExporter],
) -> None:
    handler, exporter = export
    configure_logging("INFO")
    get_logger("t").info("before")

    discard_early_records()

    assert release_early_records(handler) == 0
    assert exporter.get_finished_logs() == ()


def test_a_process_that_never_decides_holds_a_bounded_number_and_says_how_many_it_lost() -> None:
    held = EarlyRecords(capacity=2)
    for index in range(5):
        held.emit(
            logging.LogRecord(EXPORT_LOGGER, logging.INFO, __file__, 0, str(index), None, None)
        )

    records, dropped = held.take()

    assert [r.getMessage() for r in records] == ["0", "1"]
    assert dropped == 3


def test_configure_observability_hands_the_held_lines_to_its_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wire: `configure_observability` is what releases them, into the handler it attaches."""
    from aira_common.observability import providers

    configure_logging("INFO")
    get_logger("t").warning("vault_dev_token_used")
    seen: list[logging.Handler] = []
    made: list[LoggerProvider] = []
    monkeypatch.setattr(providers, "release_early_records", seen.append)
    monkeypatch.setattr(providers, "_configured", False)
    monkeypatch.setattr(providers, "set_logger_provider", made.append)

    try:
        providers.configure_observability(
            service_name="t", logs_endpoint="http://127.0.0.1:1/v1/logs"
        )
    finally:
        for provider in made:
            provider.shutdown()

    assert len(seen) == 1 and isinstance(seen[0], LoggingHandler)
    logging.getLogger().removeHandler(seen[0])
