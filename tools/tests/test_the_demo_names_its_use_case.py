"""The demo's traffic names the use case it belongs to, as every model call must (`FRD-149`).

`demo_traffic.ask` and `embed` send with each use case's own key. Before every model call had to
name its use case, the key alone placed a request; now a request without `/uc/<slug>` is refused,
and the showcase would fill its charts with nothing but refusals while reporting each one as the
demo working. Asserted on what the functions send, captured by a transport, not on the source.
"""

from __future__ import annotations

import asyncio

import demo_traffic
import httpx


def _sent(call: str) -> httpx.Request:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
            if call == "ask":
                await demo_traffic.ask(client, "kundenservice", "Hallo")
            else:
                await demo_traffic.embed(client, "kundenservice")

    asyncio.run(run())
    (request,) = seen
    return request


def test_a_question_names_its_use_case() -> None:
    assert "/uc/kundenservice/v1beta/models/" in str(_sent("ask").url)


def test_an_embedding_names_its_use_case() -> None:
    assert "/uc/kundenservice/v1beta/models/" in str(_sent("embed").url)
