"""An answer the model gave is returned when its audit row cannot be written (`FRD-128`).

By the time the row is handed to the writer the upstream has answered and the budget has booked the
cost. A full writer queue writes the row inline, and a database that is slow enough to fill the
queue is one that may fail that write: answering *that* with "Internal error" sends the caller to
retry and pay again — the retry storm `record_refusal` already refuses to invite for a refusal. So
the loss is logged, by name and without values, and the answer goes out.

The exception is the installation that asked for it: `AIRA_LOG_QUEUE_SIZE=0` means every row is
durable before its response, and an answer it could not record is one it does not return.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aira_common.logging import configure_logging
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings

BODY = {"contents": [{"role": "user", "parts": [{"text": "hello"}]}]}


def _app(**settings: Any) -> Any:
    return create_app(GatewaySettings(auth_required=False, demo_mode=True, **settings))


async def _database_away(_entry: object) -> None:
    raise RuntimeError("the audit database is away")


def _events(capsys: Any) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip().startswith("{")
    ]


@pytest.mark.parametrize(
    "verb", [":generateContent", ":streamGenerateContent?alt=sse"], ids=["buffered", "streamed"]
)
def test_a_served_answer_survives_a_failed_audit_write(verb: str, capsys: Any) -> None:
    configure_logging("INFO", json_output=True)
    app = _app(log_queue_size=8)
    with TestClient(app) as client:
        app.state.log_writer.submit = _database_away
        response = client.post(f"/v1beta/models/mock-1{verb}", json=BODY)

    assert response.status_code == 200, response.text
    assert "mock:mock-1" in response.text, "the answer itself did not reach the caller"
    (lost,) = [event for event in _events(capsys) if event["event"] == "audit_row_not_recorded"]
    assert (lost["model"], lost["status"]) == ("mock-1", 200)


def test_an_installation_that_asked_for_every_row_first_gets_the_failure() -> None:
    """A queue of zero is the operator's statement that an unrecorded answer must not go out."""
    app = _app(log_queue_size=0)
    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.log_writer.submit = _database_away
        response = client.post("/v1beta/models/mock-1:generateContent", json=BODY)

    assert response.status_code == 500


async def _store_away(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError("the budget store is away")


@pytest.mark.parametrize(
    "verb", [":generateContent", ":streamGenerateContent?alt=sse"], ids=["buffered", "streamed"]
)
def test_an_answer_and_its_row_survive_a_failed_booking(verb: str, capsys: Any) -> None:
    """The booking runs before the row is written, and a booking that raised used to take the row
    with it: a served, billed request answered `500` and left no evidence at all — the shape a
    running total too large for its column produced on every request of a busy use case."""
    configure_logging("INFO", json_output=True)
    app = _app(log_queue_size=8)
    written: list[Any] = []
    with TestClient(app) as client:
        app.state.budgets.settle = _store_away

        async def keep(entry: Any) -> None:
            written.append(entry)

        app.state.log_writer.submit = keep
        response = client.post(f"/v1beta/models/mock-1{verb}", json=BODY)

    assert response.status_code == 200, response.text
    assert "mock:mock-1" in response.text, "the answer itself did not reach the caller"
    assert len(written) == 1, "the request left no audit row"
    (failed,) = [e for e in _events(capsys) if e["event"] == "budget_settlement_failed"]
    assert failed["error_type"] == "RuntimeError"
    assert "away" not in json.dumps(failed), "the log line carries the exception's text"
