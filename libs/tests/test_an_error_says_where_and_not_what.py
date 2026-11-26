"""What of an error a line says: its type and where, never its values below `DEBUG` (`FRD-631`
FR-8).

A database's message quotes the key a constraint refused — `Key (username)=(max.mustermann) already
exists` — and the whole row a NOT NULL constraint refused, which may be somebody's prompt. A
traceback ends in that message. So a line says the error's type, where in AIRA's code it was raised
and, for a database error, the constraint and the SQLSTATE; the message and the traceback only at
`AIRA_LOG_LEVEL=DEBUG`.
"""

from __future__ import annotations

import json
from typing import Any

from aira_common.logging import configure_logging, database_diagnostics, get_logger, loggable

MESSAGE = 'duplicate key value violates unique constraint "auth_user_username_key"'
DETAIL = "\nDETAIL:  Key (username)=(max.mustermann) already exists."


class Diag:
    constraint_name = "auth_user_username_key"
    table_name = "auth_user"


class UniqueViolation(Exception):
    """Shaped as psycopg's: the diagnostics on `diag`, the class of error in `sqlstate`."""

    sqlstate = "23505"
    diag = Diag()


class IntegrityError(Exception):
    """Shaped as Django's and SQLAlchemy's: the driver's error is the cause."""


def _failing() -> None:
    try:
        raise UniqueViolation(MESSAGE + DETAIL)
    except UniqueViolation as driver:
        raise IntegrityError(MESSAGE + DETAIL) from driver


def _logged(capsys: Any, level: str) -> dict[str, Any]:
    configure_logging(level, json_output=True)
    try:
        _failing()
    except IntegrityError as exc:
        get_logger("t").error("unhandled_error", exc_info=exc)
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_at_info_a_line_says_the_type_the_place_and_the_constraint_and_no_value(
    capsys: Any,
) -> None:
    line = _logged(capsys, "INFO")

    assert line["error_type"] == "IntegrityError"
    assert line["error_at"].startswith("test_an_error_says_where_and_not_what.py:")
    assert line["error_at"].endswith("in _failing")
    assert (line["constraint"], line["table"], line["sqlstate"]) == (
        "auth_user_username_key",
        "auth_user",
        "23505",
    )
    assert "mustermann" not in json.dumps(line)
    assert "exception" not in line


def test_at_debug_the_traceback_with_its_message_is_written_too(capsys: Any) -> None:
    line = _logged(capsys, "DEBUG")

    assert "Traceback" in line["exception"]
    assert "mustermann" in line["exception"]


def test_a_databases_detail_is_cut_from_a_message_written_on_purpose() -> None:
    assert loggable(Exception(MESSAGE + DETAIL)) == MESSAGE
    assert loggable(Exception("integer out of range")) == "integer out of range"


def test_an_error_with_no_diagnostics_has_none() -> None:
    assert database_diagnostics(ValueError("x")) == {}


def test_the_diagnostics_are_found_down_the_chain() -> None:
    try:
        _failing()
    except IntegrityError as exc:
        assert database_diagnostics(exc)["constraint"] == "auth_user_username_key"
