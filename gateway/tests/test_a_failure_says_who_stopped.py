"""A failure says who stopped: the provider, this gateway, or the request.

**What was wrong.** A caller of Claude on Vertex read `Vertex upstream returned 529.` with the
status `502 UNAVAILABLE`, or `Vertex upstream error: ReadTimeout.` with the same — and could not
tell whether the provider was down, the gateway was broken, or the request was at fault. Three
things were behind it:

- **Anthropic's `529` "overloaded" was a status nothing knew.** It did not move a regional failover
  on, as a `503` does, and reached the caller as a masked `502`; its explanation was "The provider
  refused."
- **The gateway's own time limit read as the provider's failure.** A non-streamed answer that
  took longer than `AIRA_VERTEX_TIMEOUT_SECONDS` — a large context, long thinking — ended in a
  `502` that said `ReadTimeout`, which a caller reads as "the provider is unreachable". It was this
  gateway that stopped waiting.
- **A provider's `413` was masked as `502`**, so a request too large for the provider was retried
  as it was, with no word of why.

So a message now begins with **who acted** — the provider by its name, or AIRA — and the status says
the same: the provider's own where it gave one, the gateway's `504` where its time limit ran out.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_common.tokens import StaticTokenSource
from aira_gateway.api.serving import upstream_answer
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError, stream_failure
from aira_gateway.upstreams.exchange import refused, timeout, unreachable
from aira_gateway.upstreams.vertex.adapters import VertexAnthropicAdapter
from aira_gateway.upstreams.vertex.regions import VertexModel, _across_regions
from aira_gateway.upstreams.vertex.transport import NAME, VertexTransport

MODEL = "claude-sonnet-4-6"
NUMERIC_ID = 9201

#: Anthropic's own error envelope, which Claude on Vertex answers with.
OVERLOADED = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
TOO_LARGE = {
    "type": "error",
    "error": {
        "type": "request_too_large",
        "message": "Request exceeds the maximum allowed number of bytes.",
    },
}

GEMINI = f"/v1beta/models/{MODEL}:generateContent"
GEMINI_BODY = {"contents": [{"role": "user", "parts": [{"text": "read the repository"}]}]}
KIRA = "/kira/api/external/chat"
KIRA_BODY = {"request": {"parts": [{"text": "read the repository"}]}, "model_id": NUMERIC_ID}


def _request() -> httpx.Request:
    """A request as httpx records it for a client with the Vertex adapter's default allowance."""
    return httpx.Request(
        "POST",
        "https://europe-west1-aiplatform.googleapis.com/v1/x",
        extensions={"timeout": timeout(120.0).as_dict()},
    )


# == the provider, by name =========================================================================


def test_an_overloaded_claude_is_the_providers_and_says_so() -> None:
    error = refused(httpx.Response(529, json=OVERLOADED, request=_request()), NAME)

    assert error.message == "Google Vertex AI returned 529. Reason: overloaded_error."
    assert error.status_code == 529
    assert error.diagnosis is not None
    assert "overloaded" in error.diagnosis.explanation
    assert "Not a fault of the request or of this installation" in error.diagnosis.explanation


def test_a_providers_own_error_type_is_explained_where_its_status_says_nothing() -> None:
    """A `500` says nothing a bare status table can explain; the provider's `api_error` does —
    and it is the provider's, not this installation's."""
    failed = {"type": "error", "error": {"type": "api_error", "message": "Internal server error"}}
    error = refused(httpx.Response(500, json=failed, request=_request()), NAME)

    assert error.message == "Google Vertex AI returned 500. Reason: api_error."
    assert error.diagnosis is not None
    assert error.diagnosis.explanation == "The provider failed internally. Retry later."


def test_an_overloaded_claude_is_unavailable_to_its_caller_not_a_masked_failure() -> None:
    """`529` means nothing to a client; `503 UNAVAILABLE` says *try again*."""
    error = refused(httpx.Response(529, json=OVERLOADED, request=_request()), NAME)

    assert upstream_answer(error) == (503, "UNAVAILABLE")


async def test_an_overloaded_claude_moves_on_to_the_next_region() -> None:
    """Overloaded *here* is a fact about the place, as a `503` is: another region may answer."""
    asked: list[str] = []

    async def attempt(region: str, publisher: str) -> str:
        asked.append(region)
        if region == "europe-west1":
            raise UpstreamError("Google Vertex AI returned 529.", 529)
        return "answered"

    targets = (("europe-west1", "anthropic"), ("europe-west4", "anthropic"))
    assert await _across_regions(targets, attempt) == "answered"
    assert asked == ["europe-west1", "europe-west4"]


def test_a_request_too_large_for_the_provider_is_the_callers_to_shrink_and_says_why() -> None:
    error = refused(httpx.Response(413, json=TOO_LARGE, request=_request()), NAME)

    assert error.message.startswith("Google Vertex AI returned 413.")
    # The provider's own reason, as for a 400: it is about the request, and it is what to change.
    assert "Request exceeds the maximum allowed number of bytes." in error.message
    assert upstream_answer(error) == (413, "INVALID_ARGUMENT")


def test_an_answer_the_provider_broke_off_says_it_was_the_provider() -> None:
    error = stream_failure({"type": "overloaded_error", "message": "Overloaded"})

    assert error.message == "The model provider broke off the answer mid-stream: overloaded_error."
    assert upstream_answer(error) == (503, "UNAVAILABLE")


# == the gateway, when it was the gateway ==========================================================


def test_the_gateways_own_time_limit_says_aira_stopped_waiting_and_for_how_long() -> None:
    error = unreachable(httpx.ReadTimeout("timed out", request=_request()), NAME)

    assert error.message == (
        "AIRA stopped waiting: Google Vertex AI sent nothing for 120 s, the longest this gateway "
        "waits for it. Reason: READ_TIMEOUT."
    )
    assert upstream_answer(error) == (504, "DEADLINE_EXCEEDED")
    # Not a status the provider gave: a second region asked now would start the same long answer
    # again, and both would be billed.
    assert error.status_code is None
    assert (
        error.diagnosis is not None and "AIRA_VERTEX_TIMEOUT_SECONDS" in error.diagnosis.explanation
    )


def test_a_full_connection_pool_is_the_gateways_and_is_unavailable() -> None:
    error = unreachable(httpx.PoolTimeout("no connection", request=_request()), NAME)

    assert error.message.startswith("AIRA had no free connection to Google Vertex AI within 120 s")
    assert upstream_answer(error) == (503, "UNAVAILABLE")
    assert error.diagnosis is not None and error.diagnosis.reason == "POOL_EXHAUSTED"


def test_a_provider_that_cannot_be_reached_says_aira_could_not_reach_it() -> None:
    error = unreachable(httpx.ConnectError("refused", request=_request()), NAME)

    assert error.message.startswith("AIRA could not reach Google Vertex AI (ConnectError).")
    assert upstream_answer(error) == (502, "UNAVAILABLE")


# == what each surface answers =====================================================================


def _app(answer: Callable[[httpx.Request], httpx.Response]) -> Any:
    """Claude on Vertex, behind a transport that answers with ``answer`` — the real adapter, the
    real transport, the real judgement of what came back."""
    transport = VertexTransport(
        project="p",
        tokens=StaticTokenSource("t"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(answer), timeout=timeout(120.0)),
    )
    adapter = VertexAnthropicAdapter(
        transport, [VertexModel("europe-west1", "anthropic", MODEL)], default_max_tokens=1024
    )
    app = create_app(GatewaySettings(auth_required=False, require_use_case=False, log_queue_size=0))
    app.state.providers = ProviderRegistry([adapter])
    return app


def _times_out(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


ANSWERS: dict[str, Callable[[httpx.Request], httpx.Response]] = {
    "overloaded": lambda request: httpx.Response(529, json=OVERLOADED),
    "timed-out": _times_out,
    "too-large": lambda request: httpx.Response(413, json=TOO_LARGE),
}


@pytest.mark.parametrize(
    ("answer", "status", "word", "said"),
    [
        ("overloaded", 503, "UNAVAILABLE", "Google Vertex AI returned 529"),
        ("timed-out", 504, "DEADLINE_EXCEEDED", "AIRA stopped waiting"),
        ("too-large", 413, "INVALID_ARGUMENT", "maximum allowed number of bytes"),
    ],
)
def test_the_gemini_surface_answers_with_who_stopped(
    answer: str, status: int, word: str, said: str
) -> None:
    app = _app(ANSWERS[answer])
    with TestClient(app) as client:

        async def seed() -> None:
            async with app.state.db_sessionmaker() as session:
                session.add(
                    ModelRead(
                        model=MODEL, numeric_id=NUMERIC_ID, approved=True, capabilities=["generate"]
                    )
                )
                await session.commit()

        client.portal.call(seed)  # type: ignore[attr-defined]
        response = client.post(GEMINI, json=GEMINI_BODY)

        async def rows() -> list[RequestLog]:
            async with app.state.db_sessionmaker() as session:
                return list((await session.execute(select(RequestLog))).scalars())

        [row] = client.portal.call(rows)  # type: ignore[attr-defined]

    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["status"] == word
    assert said in error["message"]
    assert (row.status, row.outcome) == (status, "upstream_error")
    assert said in (row.error or "")


@pytest.mark.parametrize("answer", sorted(ANSWERS))
def test_the_kira_surface_keeps_its_one_status_and_says_who_stopped(answer: str) -> None:
    """The predecessor answers every provider failure but a `429` with `502 EXTERNAL_KI_API_ERROR`,
    and a migrating client switches on that; what changed is the message it carries."""
    app = _app(ANSWERS[answer])
    with TestClient(app) as client:

        async def seed() -> None:
            async with app.state.db_sessionmaker() as session:
                session.add(
                    ModelRead(
                        model=MODEL, numeric_id=NUMERIC_ID, approved=True, capabilities=["generate"]
                    )
                )
                await session.commit()

        client.portal.call(seed)  # type: ignore[attr-defined]
        response = client.post(KIRA, json=KIRA_BODY)

    assert response.status_code == 502, response.text
    body = response.json()
    assert body["code"] == "EXTERNAL_KI_API_ERROR"
    assert body["message"].startswith("No model answered this request")
    assert "Google Vertex AI" in body["message"]
