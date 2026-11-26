"""An unexpected error's line says its type and where, not what it was about (`FRD-631` FR-8).

The last-resort handler wrote `error=str(exc)`: a database's message quotes the row a constraint
refused — `Key (username)=(max.mustermann) already exists` — so a defect wrote somebody's data into
a log with no role check and no retention clock. Now the line carries the type and the place, and
the message only at `AIRA_LOG_LEVEL=DEBUG`.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import structlog
from fastapi.testclient import TestClient

from aira_gateway import exception_handlers
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings

QUOTED = "Key (username)=(max.mustermann) already exists"


def _unhandled(capsys: Any, monkeypatch: pytest.MonkeyPatch, level: str) -> dict[str, Any]:
    app = create_app(
        GatewaySettings(auth_required=False, log_level=level, pseudonym_secret="the suite's key")
    )
    monkeypatch.setattr(exception_handlers, "_log", structlog.get_logger("aira_gateway"))

    @app.get("/boom")  # type: ignore[untyped-decorator]
    async def boom() -> None:
        raise RuntimeError(QUOTED)

    with TestClient(app, raise_server_exceptions=False) as client:
        capsys.readouterr()
        assert client.get("/boom").status_code == 500
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line[:1] == "{"]
    (line,) = [line for line in lines if line["event"] == "unhandled_error"]
    return line


def test_at_info_the_line_has_the_type_and_the_place_and_not_the_message(
    capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    line = _unhandled(capsys, monkeypatch, "INFO")

    assert line["error_type"] == "RuntimeError"
    # The innermost frame of AIRA's own code — this test's route is not, so the middleware that
    # called it is where the line points; a defect in the gateway is found where it was raised.
    assert line["error_at"].startswith("aira_gateway/") and ":" in line["error_at"]
    assert line["path"] == "/boom"
    assert "mustermann" not in json.dumps(line)
    assert "withheld_fields" not in line


def test_at_debug_the_message_and_traceback_are_there_to_reproduce_it(
    capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    line = _unhandled(capsys, monkeypatch, "DEBUG")

    assert QUOTED in line["exception"]
