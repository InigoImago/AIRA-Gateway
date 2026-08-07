"""A KIRA stream that is cancelled mid-flight is still accounted for (FRD-127).

Found by assessing what a third API surface would cost, not by a failure: the **post**-dispatch
sequence — hold, dispatch, check, price, settle, record — is written out once per verb per surface,
and the Gemini streaming path had earned a `finally` + `asyncio.shield` from an integration flake
that the KIRA one never received. The surface written second did not get the fix the first one
earned, which is what duplication does and where it leaves it.

The failure window is different here, and the difference is why a copy of the Gemini test would
have proved nothing. This surface's "stream" delivers **one terminal event carrying the whole
answer**, so its accounting happens *before* anything is yielded: hanging up after the first chunk
finds the work already done. What is exposed is the long await in the middle — a caller that goes
away while the model is still thinking. The task is cancelled, the `hold` gives the reservation
back, and the request reaches the upstream and then vanishes from the record.

So the test cancels during dispatch, which is the only place the gap is.
"""

from __future__ import annotations

import asyncio
import contextlib

from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalRequest, CanonicalResponse, CanonicalUsage
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel

_BODY = {"request": {"parts": [{"text": "hi"}]}, "model_id": 1}


class _SlowProvider:
    """A model that takes its time, so a caller can go away while it is still answering."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.finish = asyncio.Event()
        self.calls = 0

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("slow-1", "1", ("generateContent",), provider="test", region="eu")]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        self.calls += 1
        self.entered.set()
        await self.finish.wait()
        return CanonicalResponse(
            model="slow-1",
            text="done",
            usage=CanonicalUsage(prompt_tokens=3, completion_tokens=4),
        )

    async def stream_generate(self, request: CanonicalRequest):  # noqa: ANN201
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


async def test_a_caller_that_goes_away_while_the_model_answers_is_still_recorded() -> None:
    """The upstream was called. Whether the caller stayed to hear the answer does not change that,
    and a request that reached a model and left no row is the one thing `FRD-122` exists to
    prevent.
    """
    provider = _SlowProvider()
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0, allowed_regions="eu"))
    app.state.providers = ProviderRegistry([provider])

    with TestClient(app):
        async with app.state.db_sessionmaker() as session:
            session.add(ModelRead(model="slow-1", numeric_id=1, capabilities=["generate"]))
            await session.commit()

        # Driven as a task rather than through TestClient, which would buffer the whole body and
        # never let the test cancel at the point that matters.
        from aira_gateway.api.kira import routes

        request = _kira_request(app)
        task = asyncio.create_task(_drive(routes, request, None))
        await asyncio.wait_for(provider.entered.wait(), timeout=5)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        provider.finish.set()
        await asyncio.sleep(0.1)

        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())

    assert provider.calls == 1, "the model was never reached, so this test proves nothing"
    assert rows, "a cancelled stream reached the upstream and vanished from the audit log"
    # 499, not 200: nobody was served, and the audit has to be able to tell those apart.
    assert rows[0].status == 499
    assert rows[0].operation == "streaming-chat"


async def test_a_cancelled_stream_is_not_billed_for_what_it_did_not_deliver() -> None:
    """Nothing chargeable was produced, so nothing is booked. A use case with a request limit must
    not lose allowance to a caller who hung up.

    Asserted on the **counter**, not on a call to `release`. A tracking stand-in cannot see it:
    `hold` is the real service's own method and its internal `self.release(...)` never passes
    through a wrapper — so a test that counted calls would be testing the wrapper.
    """
    from aira_gateway.db.models import BudgetRead, BudgetUsage

    provider = _SlowProvider()
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0, allowed_regions="eu"))
    app.state.providers = ProviderRegistry([provider])

    with TestClient(app):
        async with app.state.db_sessionmaker() as session:
            session.add(ModelRead(model="slow-1", numeric_id=1, capabilities=["generate"]))
            session.add(
                BudgetRead(
                    id=1,
                    use_case="demo",
                    scope="use_case",
                    subject="",
                    period="month",
                    limit_requests=1000,
                    enabled=True,
                )
            )
            await session.commit()

        from aira_gateway.api.kira import routes

        request = _kira_request(app, use_case="demo")
        task = asyncio.create_task(_drive(routes, request, None))
        await asyncio.wait_for(provider.entered.wait(), timeout=5)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        provider.finish.set()
        await asyncio.sleep(0.1)

        async with app.state.db_sessionmaker() as session:
            usage = list((await session.execute(select(BudgetUsage))).scalars())

    assert not any(u.requests for u in usage), "a caller who received nothing was billed"


def _kira_request(app, use_case: str | None = None):  # noqa: ANN001, ANN202
    """A minimal ASGI request carrying the state the streaming handler reads."""
    import json as _json

    from fastapi import Request

    body = _json.dumps(_BODY).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/kira/api/external/streaming-chat",
        "headers": [(b"content-type", b"application/json")],
        "query_string": b"",
        "client": ("127.0.0.1", 1234),
        "app": app,
        "state": {},
    }

    async def receive():  # noqa: ANN202
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


async def _drive(routes, request, principal):  # noqa: ANN001, ANN202
    """Run the handler and consume its stream, so the generator actually starts."""
    from aira_gateway.auth.dependencies import _DEMO_PRINCIPAL

    response = await routes.streaming_chat(request, principal or _DEMO_PRINCIPAL)
    async for _ in response.body_iterator:
        pass
