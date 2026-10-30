"""A request's technical record: codes and numbers a technician can follow (`FRD-630`).

The sentence a caller is told says what happened; this says where it ended, at which millisecond,
which exception or provider status word stood behind it, and the provider's own id for the request.
**Never a sentence from outside the gateway** — a provider's message can quote the prompt, or a URL
with a key in it — so every case that feeds one in checks that it did not come out.

The stream's milestones are watched on the wire in `test_a_quiet_model_keeps_its_connection.py`;
these are the record's own rules, the provider's facts, and the detail that serves them.
"""

from __future__ import annotations

import errno
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import RequestLog
from aira_gateway.persistence.recorder import record_request
from aira_gateway.technical import PHASES, Technical, phase_of, technical_of
from aira_gateway.upstreams.base import UpstreamError, stream_failure
from aira_gateway.upstreams.exchange import refused, unreachable

BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}

#: Something a provider's message quotes back that must never reach the record: a prompt's words,
#: and a key in a URL.
SECRET_PROMPT = "the quarterly figures of Ada Lovelace"
SECRET_KEY = "AIzaSy-not-for-the-record"
ADDRESS = f"https://europe-west1-aiplatform.googleapis.com/v1/models/x:generate?key={SECRET_KEY}"


def _leaks(record: Any) -> bool:
    text = json.dumps(record)
    return SECRET_PROMPT in text or SECRET_KEY in text or "?" in text


# == where a request ended =======================================================================


@pytest.mark.parametrize(
    ("marks", "failed", "phase"),
    [
        ({"dispatched": 1, "provider_first_chunk": 5, "provider_finished": 9}, False, "completed"),
        ({}, True, "before_dispatch"),
        ({"dispatched": 1}, True, "waiting_for_provider"),
        (
            {"dispatched": 1, "caller_left": 4, "provider_first_chunk": 9},
            False,
            "waiting_for_provider",
        ),
        ({"dispatched": 1, "provider_first_chunk": 2, "caller_left": 4}, False, "mid_answer"),
        (
            {"dispatched": 1, "provider_first_chunk": 2, "caller_left": 4, "provider_finished": 9},
            False,
            "mid_answer",
        ),
        (
            {"dispatched": 1, "provider_first_chunk": 2, "provider_finished": 3, "caller_left": 4},
            False,
            "after_provider_finished",
        ),
        # An answer read whole: finished and first at once.
        ({"dispatched": 1, "provider_finished": 3}, True, "after_provider_finished"),
    ],
)
def test_the_phase_is_where_the_request_was_when_it_ended(
    marks: dict[str, int], failed: bool, phase: str
) -> None:
    assert phase_of(marks, failed=failed) == phase
    assert phase in PHASES


def test_a_request_nothing_happened_to_keeps_no_record() -> None:
    assert Technical().snapshot() is None


def test_a_milestone_keeps_its_first_moment_and_the_last_byte_its_last() -> None:
    technical = Technical()
    technical.mark("dispatched")
    first = technical.snapshot()
    technical.mark("dispatched")
    technical.wrote(10)
    technical.wrote(0)
    technical.wrote(5)
    technical.keepalive()
    record = technical.snapshot()

    assert first is not None and record is not None
    assert record["timeline_ms"]["dispatched"] == first["timeline_ms"]["dispatched"]
    # An empty write sent nothing: it is neither a byte nor a write.
    assert record["to_caller"] == {"bytes": 15, "writes": 2, "keepalives": 1}


# == what it failed with =========================================================================


def test_a_connection_reset_names_the_chain_and_the_operating_systems_word() -> None:
    technical = Technical()
    try:
        try:
            raise ConnectionResetError(errno.ECONNRESET, "Connection reset by peer")
        except ConnectionResetError as reset:
            raise httpx.ReadError(f"reset while reading {ADDRESS}") from reset
    except httpx.ReadError as exc:
        technical.failed(exc)
    record = technical.snapshot()

    assert record is not None
    assert record["exception"] == {
        "chain": ["httpx.ReadError", "ConnectionResetError"],
        "errno": "ECONNRESET",
    }
    assert not _leaks(record)


def test_a_failure_of_the_gateways_own_names_its_line_and_never_its_text() -> None:
    technical = Technical()
    try:
        phase_of(None, failed=True)  # type: ignore[arg-type]
    except AttributeError as exc:
        technical.failed(exc)
    record = technical.snapshot()

    assert record is not None
    assert record["exception"]["chain"] == ["AttributeError"]
    assert record["exception"]["at"].startswith("aira_gateway/technical.py:")
    assert record["exception"]["at"].endswith(" in phase_of")


def test_anything_that_is_not_a_code_is_dropped_rather_than_kept() -> None:
    technical = Technical()
    error = UpstreamError("refused", 400)
    error.facts = {
        "status": "400",  # a number, as text
        "reason": f"Your prompt: {SECRET_PROMPT}?",  # a sentence
        "request_id": "req_011CU" + "x" * 300,  # longer than an id
        "host": "aiplatform.googleapis.com",
        "body": SECRET_PROMPT,  # not a key the record has
    }
    technical.failed(error)
    record = technical.snapshot()

    assert record is not None
    # The status the error itself carries stands in for the one that was not a number.
    assert record["provider"] == {"status": 400, "host": "aiplatform.googleapis.com"}
    assert not _leaks(record)


# == the provider's facts ========================================================================


def test_a_refusal_carries_the_providers_status_word_request_id_and_retry_after() -> None:
    response = httpx.Response(
        429,
        headers={"request-id": "req_011CUabc", "retry-after": "30"},
        json={"type": "error", "error": {"type": "rate_limit_error", "message": SECRET_PROMPT}},
        request=httpx.Request("POST", ADDRESS),
    )
    technical = Technical()
    technical.failed(refused(response, "Google Vertex AI"))
    record = technical.snapshot()

    assert record is not None
    assert record["provider"] == {
        "status": 429,
        "reason": "rate_limit_error",
        "request_id": "req_011CUabc",
        "retry_after": "30",
        "host": "europe-west1-aiplatform.googleapis.com",
    }
    # A provider's refusal names no line of ours: the provider stated it.
    assert "at" not in record["exception"]
    assert not _leaks(record)


def test_googles_status_word_is_the_reason_where_it_states_one() -> None:
    response = httpx.Response(
        429,
        json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": SECRET_PROMPT}},
        request=httpx.Request("POST", ADDRESS),
    )
    technical = Technical()
    technical.failed(refused(response, "Google Vertex AI"))
    record = technical.snapshot()

    assert record is not None and record["provider"]["reason"] == "RESOURCE_EXHAUSTED"
    assert not _leaks(record)


def test_a_time_limit_names_the_host_the_cause_and_the_chain() -> None:
    technical = Technical()
    request = httpx.Request("POST", ADDRESS)
    try:
        try:
            raise httpx.ReadTimeout("timed out", request=request)
        except httpx.ReadTimeout as timeout:
            raise unreachable(timeout, "Google Vertex AI") from timeout
    except UpstreamError as exc:
        technical.failed(exc)
    record = technical.snapshot()

    assert record is not None
    assert record["provider"]["host"] == "europe-west1-aiplatform.googleapis.com"
    assert "status" not in record["provider"], "nobody answered: there is no status to name"
    assert record["exception"]["chain"] == [
        "aira_gateway.upstreams.base.UpstreamError",
        "httpx.ReadTimeout",
    ]
    assert not _leaks(record)


def test_an_error_inside_a_stream_says_so_and_names_its_type() -> None:
    technical = Technical()
    technical.failed(stream_failure({"type": "overloaded_error", "message": SECRET_PROMPT}))
    record = technical.snapshot()

    assert record is not None
    assert record["provider"]["reason"] == "overloaded_error"
    assert record["provider"]["where"] == "in_stream"
    assert not _leaks(record)


# == on the row, and in the request's details ====================================================


def _app() -> Any:
    return create_app(GatewaySettings(auth_required=False, log_queue_size=0))


async def _rows(app: Any) -> list[RequestLog]:
    async with app.state.db_sessionmaker() as session:
        return list((await session.execute(select(RequestLog))).scalars())


def test_an_answered_request_is_recorded_and_served_in_its_details_only() -> None:
    app = _app()
    with TestClient(app) as client:
        answered = client.post("/v1beta/models/mock-1:generateContent", json=BODY)
        listed = client.get("/v1beta/traces").json()["traces"][0]
        detail = client.get(f"/v1beta/traces/{answered.headers['x-aira-request-id']}").json()
        [row] = client.portal.call(_rows, app)  # type: ignore[attr-defined]

    record = detail["trace"]["technical"]
    assert record == row.technical
    assert record["phase"] == "completed"
    timeline = record["timeline_ms"]
    assert timeline["dispatched"] <= timeline["provider_finished"] <= timeline["recorded"]
    # Recorded before the answer went out: nothing had reached the caller yet.
    assert "to_caller" not in record
    assert "technical" not in listed


def test_a_refusal_before_any_model_names_the_control_that_raised_it() -> None:
    app = _app()
    with TestClient(app) as client:
        client.post("/v1beta/models/no-such-model:generateContent", json=BODY)
        [row] = client.portal.call(_rows, app)  # type: ignore[attr-defined]

    assert row.technical is not None
    assert row.technical["phase"] == "before_dispatch"
    assert row.technical["exception"]["chain"][0].startswith("aira_gateway.")
    assert row.technical["exception"]["at"].startswith("aira_gateway/")


async def test_a_pipeline_steps_own_call_carries_no_record_of_the_request() -> None:
    """A step's call is recorded on a row of its own; the request's record is the request's."""
    submitted: list[Any] = []

    class _Writer:
        synchronous = False

        async def submit(self, entry: Any) -> None:
            submitted.append(entry.technical)

    app = SimpleNamespace(state=SimpleNamespace(log_writer=_Writer(), settings=GatewaySettings()))
    request = Request({"type": "http", "app": app, "headers": [], "state": {}})
    request.state.attribution = SimpleNamespace(
        subject="s", username=None, method="api_key", use_case=None, credential=None, issuer=None
    )
    technical_of(request).mark("dispatched")

    for own in (False, True):
        await record_request(
            request,
            operation="op",
            model="m",
            status=200,
            usage=None,
            latency_ms=None,
            request_payload=None,
            response_payload=None,
            api="gemini",
            technical=own,
        )

    assert submitted[0] is None
    assert submitted[1] is not None and "dispatched" in submitted[1]["timeline_ms"]
