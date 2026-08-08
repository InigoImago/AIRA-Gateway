"""A credential in the access log (`ADR-0007`, extended 2026-08-08).

`redact_span_query` has kept `?key=<api key>` out of exported traces since `ADR-0007`. The web
server's *own* access log wrote the request line verbatim — so a Gemini client authenticating the
documented way put its API key into the log of every request it made, in the place that is
collected, forwarded and readable by everyone who can read container logs.
"""

from __future__ import annotations

import logging

from aira_common.logging import configure_logging
from aira_common.observability import (
    ACCESS_LOGGERS,
    AccessLogRedaction,
    install_access_log_redaction,
)

KEY = "aira_abcd1234_00112233445566778899aabbccddeeff"


def _emit(record_args: tuple[object, ...]) -> tuple[object, ...]:
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d', record_args, None
    )
    AccessLogRedaction().filter(record)
    assert isinstance(record.args, tuple)
    return record.args


def test_an_api_key_in_the_request_line_is_redacted() -> None:
    args = _emit(("10.0.0.1:5", "POST", f"/v1beta/models/m:generateContent?key={KEY}", "1.1", 200))

    assert KEY not in str(args)
    assert "key=REDACTED" in str(args)


def test_the_rest_of_the_line_survives() -> None:
    """A redacted log that is no longer useful gets switched off. Everything but the value stays."""
    args = _emit(
        ("10.0.0.1:5", "POST", f"/v1beta/models/m:generateContent?alt=sse&key={KEY}", "1.1", 200)
    )

    assert args[0] == "10.0.0.1:5"
    assert args[1] == "POST"
    assert "/v1beta/models/m:generateContent?alt=sse&" in str(args[2])
    assert args[4] == 200


def test_a_request_without_a_query_is_untouched() -> None:
    args = _emit(("10.0.0.1:5", "GET", "/readyz", "1.1", 200))

    assert args[2] == "/readyz"


def test_the_filter_does_not_depend_on_a_positional_index() -> None:
    """uvicorn's tuple shape is uvicorn's to change; a filter pinned to `args[2]` is one an
    upgrade disables silently."""
    args = _emit((f"/x?token={KEY}", f"/y?password={KEY}"))

    assert KEY not in str(args)


def test_dictionary_style_arguments_are_redacted_too() -> None:
    record = logging.LogRecord(
        "django.server",
        logging.INFO,
        __file__,
        1,
        "%(request)s",
        ({"request": f"/a?key={KEY}"},),
        None,
    )
    AccessLogRedaction().filter(record)

    assert KEY not in str(record.args)


def test_configuring_logging_installs_it_on_every_access_logger() -> None:
    """The service never writes these records itself, so there is no code path of ours to put the
    redaction on instead — it has to be attached to the loggers the server uses."""
    for name in ACCESS_LOGGERS:
        logging.getLogger(name).filters = [
            f for f in logging.getLogger(name).filters if not isinstance(f, AccessLogRedaction)
        ]

    configure_logging()

    for name in ACCESS_LOGGERS:
        installed = logging.getLogger(name).filters
        assert any(isinstance(f, AccessLogRedaction) for f in installed), name


def test_installing_twice_does_not_stack_filters() -> None:
    install_access_log_redaction()
    install_access_log_redaction()
    installed = [
        f for f in logging.getLogger("uvicorn.access").filters if isinstance(f, AccessLogRedaction)
    ]

    assert len(installed) == 1
