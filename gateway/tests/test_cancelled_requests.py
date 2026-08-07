"""A request the caller abandoned is still a request that happened (FRD-128).

Asked directly — *have all the paths been tested with a dropped connection?* — the answer was no.
Streaming had been: Gemini's by `aclose` and by a live client walking away, KIRA's by cancellation
during dispatch. **Every non-streaming path had not**, and all four of them lost the audit row.

The rule is `FRD-122`'s and it does not care how the request ended: the log records what was
**asked**, not only what was served. The upstream was called, tokens were spent, money was spent —
and a caller hanging up made all of that disappear from the record. `FRD-125b` already settled the
same argument for a pipeline step that cost money on a request that was then blocked.

Six paths, one table. This file is the acceptance test for consolidating the post-dispatch sequence
(`FRD-128`): before it, four of the six were wrong; the shape of the fix is that there is one place
left for them to be wrong in.
"""

import asyncio
import contextlib
import json

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.auth.attribution import Attribution
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalResponse, CanonicalUsage
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel


class Slow:
    """A model that takes its time, so a caller can go away while it is still answering."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.finish = asyncio.Event()
        self.calls = 0

    def models(self) -> list[UpstreamModel]:
        return [
            UpstreamModel(
                "slow-1",
                "1",
                ("generateContent", "embedContent"),
                provider="t",
                region="eu",
            )
        ]

    async def generate(self, request):  # noqa: ANN001, ANN201
        self.calls += 1
        self.entered.set()
        await self.finish.wait()
        return CanonicalResponse(
            model="slow-1",
            text="x",
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
        )

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, request):  # noqa: ANN001, ANN201
        self.calls += 1
        self.entered.set()
        await self.finish.wait()
        return [[0.0]]


def req(app, path, body):
    raw = json.dumps(body).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"content-type", b"application/json")],
        "query_string": b"",
        "client": ("127.0.0.1", 1),
        "app": app,
        "state": {},
        "path_params": {},
    }

    async def receive():  # noqa: ANN202
        return {"type": "http.request", "body": raw, "more_body": False}

    request = Request(scope, receive)
    # Set by the auth dependency in a real call. Driving the handler directly is the only way to
    # cancel at the moment that matters — through `TestClient` the whole response is buffered
    # first, so the test would never reach the path it is about.
    request.state.attribution = Attribution(subject="demo", method="demo", use_case=None)
    return request


CASES = [
    (
        "gemini generateContent",
        "gemini",
        "slow-1:generateContent",
        {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
    ),
    (
        "gemini embedContent",
        "gemini",
        "slow-1:embedContent",
        {"content": {"parts": [{"text": "hi"}]}},
    ),
    ("kira chat", "kira", None, {"request": {"parts": [{"text": "hi"}]}, "model_id": 1}),
    ("kira embed", "kira", None, {"text": "hi", "model_id": 1}),
]


@pytest.mark.parametrize("label,surface,resource,body", CASES)
async def test_cancelled(label, surface, resource, body):
    provider = Slow()
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0, allowed_regions="eu"))
    app.state.providers = ProviderRegistry([provider])
    with TestClient(app):
        async with app.state.db_sessionmaker() as session:
            session.add(ModelRead(model="slow-1", numeric_id=1, capabilities=["generate", "embed"]))
            await session.commit()
        from aira_gateway.auth.dependencies import _DEMO_PRINCIPAL

        if surface == "gemini":
            from aira_gateway.api.gemini.routes import generate

            r = req(app, f"/v1beta/models/{resource}", body)
            coro = generate(resource, r)
        elif "chat" in label:
            from aira_gateway.api.kira.routes import chat

            coro = chat(req(app, "/kira/api/external/chat", body), _DEMO_PRINCIPAL)
        else:
            from aira_gateway.api.kira.routes import embed

            coro = embed(req(app, "/kira/api/external/embed", body), _DEMO_PRINCIPAL)
        task = asyncio.create_task(coro)
        await asyncio.wait_for(provider.entered.wait(), timeout=5)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        provider.finish.set()
        await asyncio.sleep(0.15)
        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())
    assert provider.calls == 1, f"{label}: upstream never reached"
    assert rows, f"{label}: CANCELLED REQUEST LEFT NO AUDIT ROW"
