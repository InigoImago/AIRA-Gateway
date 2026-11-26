"""A line AIRA did not write is written as AIRA writes one, and exported once (`FRD-631` FR-2).

uvicorn's start and stop, Django's request failures, Python's warnings and a process's death went
to stdout and stderr as text, beside AIRA's JSON, and not through the export: the log store never
saw a service start. Each is a JSON line through the same pipeline now, and the export ships the
rendered line and never the raw record beside it.
"""

from __future__ import annotations

import json
import logging
import sys
import warnings
from collections.abc import Iterator
from typing import Any

import pytest
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor

from aira_common.logging import configure_logging
from aira_common.observability.providers import OnlyRenderedLines


def _lines(capsys: Any) -> list[dict[str, Any]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


@pytest.fixture
def exported() -> Iterator[InMemoryLogRecordExporter]:
    """The export as `configure_observability` attaches it: on the root logger, rendered lines
    only."""
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    handler = LoggingHandler(logger_provider=provider)
    handler.addFilter(OnlyRenderedLines())
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield exporter
    finally:
        root.removeHandler(handler)
        provider.shutdown()


def test_a_servers_start_is_a_json_line_naming_its_library(capsys: Any) -> None:
    configure_logging("INFO")

    logging.getLogger("uvicorn.error").info("Started server process [%d]", 7)

    (line,) = [line for line in _lines(capsys) if line.get("logger") == "uvicorn.error"]
    assert line["event"] == "Started server process [7]"
    assert line["level"] == "info"


def test_a_servers_access_log_is_switched_off(capsys: Any) -> None:
    """uvicorn asks `hasHandlers()` once per connection and writes nothing when there is none:
    AIRA's own access line replaces it, with the duration and without the query or the address."""
    configure_logging("INFO")

    assert not logging.getLogger("uvicorn.access").hasHandlers()


def test_the_export_ships_a_librarys_line_once_and_rendered(
    capsys: Any, exported: InMemoryLogRecordExporter
) -> None:
    configure_logging("INFO")

    logging.getLogger("uvicorn.error").info("Shutting down")

    bodies = [str(record.log_record.body) for record in exported.get_finished_logs()]
    shipped = [body for body in bodies if "Shutting down" in body]
    assert len(shipped) == 1
    assert json.loads(shipped[0])["logger"] == "uvicorn.error"


def test_djangos_warning_about_every_4xx_is_left_to_the_access_line(capsys: Any) -> None:
    """ "Unauthorized: /api/v1/me" for each refused request says what the access line already says
    with the status and the route; a server error is still a line of its own."""
    configure_logging("INFO")
    log = logging.getLogger("django.request")

    log.warning("Unauthorized: %s", "/api/v1/me", extra={"status_code": 401})
    log.error("Internal Server Error: %s", "/api/v1/x", extra={"status_code": 500})

    events = [line["event"] for line in _lines(capsys) if line.get("logger") == "django.request"]
    assert events == ["Internal Server Error: /api/v1/x"]


def test_a_warning_is_a_line(capsys: Any) -> None:
    configure_logging("INFO")
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        # Outside pytest's own recording, as in a running process.
        warnings.showwarning = logging._showwarning  # type: ignore[attr-defined]
        warnings.warn("careful", UserWarning, stacklevel=1)

    assert any(line.get("logger") == "py.warnings" for line in _lines(capsys))


def test_a_credential_in_a_librarys_message_is_cut(capsys: Any) -> None:
    configure_logging("INFO")

    logging.getLogger("uvicorn.error").warning("GET /v1beta/models?key=%s", "aira_secret")

    assert "aira_secret" not in capsys.readouterr().out


def test_a_process_that_dies_says_so_in_one_line(
    capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A start that failed — Kafka refused the credential, Vault the role — was a bare traceback
    on stderr. It is a `critical` line with what an operator needs to act on, without a database's
    detail, and the traceback only at `DEBUG`."""
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    configure_logging("INFO")

    try:
        raise RuntimeError("authentication failed for user aira_gateway")
    except RuntimeError as exc:
        sys.excepthook(type(exc), exc, exc.__traceback__)

    (line,) = [line for line in _lines(capsys) if line["event"] == "process_crashed"]
    assert line["level"] == "critical"
    assert line["error_type"] == "RuntimeError"
    assert line["error"] == "authentication failed for user aira_gateway"
    assert "exception" not in line
