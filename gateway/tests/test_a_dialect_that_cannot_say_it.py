"""A wire format that cannot express the request refuses by name, on both surfaces.

`DialectUnsupported` exists because a dialect sometimes has no field for what was asked: the
OpenAI family takes `reasoning_effort`, a word, and has no way to say *"you decide"* or *"spend
this many tokens"*; Anthropic takes `budget_tokens`, a number, and has no way to say a level. Its
own docstring called it *"unreachable in practice — a model that cannot do a thing does not
declare the capability"*.

**The console is where an administrator declares one.** Ticking `auto` for a model served by an
OpenAI-dialect endpoint takes ten seconds and is a reasonable thing to try; measured against the
running stack on 2026-08-20, every thinking request afterwards answered:

    {"error": {"code": 500, "message": "Internal error while processing the request."}}

The caller learned nothing, the operator was sent to the logs of a service working exactly as
designed, and the audit row said the gateway had broken rather than that a declaration was wrong.
The exception was raised at mapping time, one frame below the boundary — and it was not in
`REFUSALS`, which is the single list both surfaces catch.

That list is the fix, and these are the tests that would have caught it.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.api.serving import REFUSALS
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalRequest
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.upstreams.base import DialectUnsupported, ProviderRegistry, UpstreamModel

KIRA = "/kira/api/external"
REASON = (
    "This dialect has no way to say 'the model decides': `reasoning_effort` is always a level."
)


class _CannotSayIt:
    """An upstream whose mapping refuses the request, exactly as the real ones do.

    A stand-in, and marked as one. It raises the **real** exception class rather than a stub of
    it: what is under test is the boundary, and a double raising something else would prove the
    boundary handles a shape nothing produces.
    """

    is_test_double = True

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("mock-1", "mock-1", ("generateContent",))]

    async def generate(self, request: CanonicalRequest) -> object:
        raise DialectUnsupported(REASON)

    async def stream_generate(self, request: CanonicalRequest) -> Any:
        raise DialectUnsupported(REASON)
        yield  # pragma: no cover — makes this an async generator

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


def _app():  # noqa: ANN202
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    app.state.providers = ProviderRegistry([_CannotSayIt()])
    return app


async def _catalogue(app) -> None:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        session.add(
            ModelRead(
                model="mock-1",
                numeric_id=1004,
                capabilities=["generate"],
                publisher="google",
            )
        )
        await session.commit()


_GEMINI_BODY = {"contents": [{"role": "user", "parts": [{"text": "hallo"}]}]}


def test_it_is_one_of_the_refusals_both_surfaces_catch() -> None:
    """Asserted on the list rather than on a status, because the list is the rule.

    A surface that grew its own `except DialectUnsupported` would pass the two tests below and
    leave the next surface to discover the 500 again — which is the whole argument `REFUSALS`
    makes (`FRD-126`).
    """
    assert DialectUnsupported in REFUSALS


async def test_the_gemini_surface_names_the_reason_instead_of_answering_500() -> None:
    app = _app()
    with TestClient(app, raise_server_exceptions=False) as client:
        await _catalogue(app)
        response = client.post("/v1beta/models/mock-1:generateContent", json=_GEMINI_BODY)

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    # `FAILED_PRECONDITION`, the same status `NoCapableModel` answers with and for the same
    # reason: this is a configuration somebody can correct, not an outage.
    assert error["status"] == "FAILED_PRECONDITION"
    assert "reasoning_effort" in error["message"], "the dialect's own explanation reaches the caller"


async def test_the_kira_surface_answers_in_the_predecessors_envelope() -> None:
    """The other half of the same rule. A fix applied to one surface satisfies every test somebody
    thought to write about the surface they were fixing."""
    app = _app()
    with TestClient(app, raise_server_exceptions=False) as client:
        await _catalogue(app)
        response = client.post(
            f"{KIRA}/chat", json={"request": {"parts": [{"text": "hallo"}]}, "model_id": 1004}
        )

    assert response.status_code == 400, response.text
    body = response.json()
    assert "error" not in body, "Google's envelope on the compatibility surface"
    # Not `MODEL_NOT_FOUND`: the model exists and is reachable, and the request as written cannot
    # be carried — telling a client the model is missing sends it looking for a different id.
    assert body["code"] == "VALIDATION_ERROR"
    assert "reasoning_effort" in body["message"]


async def test_the_audit_row_says_a_request_was_refused_not_that_the_gateway_broke() -> None:
    """The half that matters a week later. A 500 is recorded as the gateway failing, so a report
    read by somebody investigating shows an outage where there was a wrong declaration."""
    app = _app()
    with TestClient(app, raise_server_exceptions=False) as client:
        await _catalogue(app)
        client.post("/v1beta/models/mock-1:generateContent", json=_GEMINI_BODY)

        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())

    assert [row.status for row in rows] == [400]
    assert rows[0].outcome == "invalid_request"
