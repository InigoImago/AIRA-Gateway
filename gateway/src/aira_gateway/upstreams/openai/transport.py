"""Reaching an OpenAI-compatible endpoint (`FRD-123`).

How to get there, and nothing about the API shape (`ADR-0011`). What differs from `VertexTransport`
is mostly what is **absent** — no credential, no project, no region in the URL — which is what makes
a local endpoint a development and verification tool rather than a deployment target (`FRD-123`
§8). A platform that authenticates subclasses this and overrides `headers`, as Foundry does. How an
answer is judged — status, reason, a body that is not JSON — is every platform's rule and lives in
`upstreams.exchange`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from aira_gateway.upstreams.exchange import (
    json_object,
    opened_stream,
    refused,
    timeout,
    unreachable,
)

#: Ollama loads a model on its first request, which can take a minute or more. A cold start is not
#: an outage (`ADR-0012` §5). Connecting is bounded apart (`exchange.timeout`): a machine that
#: accepts no connection is down, not warming up.
DEFAULT_TIMEOUT_SECONDS = 300.0

#: How this transport names itself in an error: "Upstream returned 429." The status passes through
#: so `UPSTREAM_STATUS_MAP` keeps its meaning — a 429 from a self-deployed endpoint means *no free
#: replica*, not quota (`ADR-0012` §5).
NAME = "Upstream"


class OpenAITransport:
    """One base URL, and no credential of its own."""

    def __init__(self, *, client: httpx.AsyncClient, timeout: float | None = None) -> None:
        self._client = client
        self._timeout = timeout or DEFAULT_TIMEOUT_SECONDS

    async def headers(self) -> dict[str, str]:
        """No credential. Async because a subclass may have to *fetch* one (`FRD-120`)."""
        return {}

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = await self.headers()
        try:
            response = await self._client.post(
                path, json=body, headers=headers, timeout=timeout(self._timeout)
            )
        except httpx.HTTPError as exc:
            raise unreachable(exc, NAME) from exc
        if response.status_code != httpx.codes.OK:
            raise refused(response, NAME)
        return json_object(response, NAME)

    async def get(self, path: str) -> dict[str, Any]:
        """A read, for listings and the readiness probe. Same credential and errors as a post."""
        headers = await self.headers()
        try:
            response = await self._client.get(path, headers=headers, timeout=timeout(self._timeout))
        except httpx.HTTPError as exc:
            raise unreachable(exc, NAME) from exc
        if response.status_code != httpx.codes.OK:
            raise refused(response, NAME)
        return json_object(response, NAME)

    @asynccontextmanager
    async def stream(self, path: str, body: dict[str, Any]) -> AsyncIterator[httpx.Response]:
        """A streamed POST, judged on entry — so a failure never surfaces after our own response
        headers have gone out — with a refusal read for its reason (`exchange.opened_stream`)."""
        headers = await self.headers()
        async with opened_stream(
            self._client, path, NAME, json=body, headers=headers, timeout=timeout(self._timeout)
        ) as response:
            yield response

    async def aclose(self) -> None:
        """Close the connection pool. Called once, from the application lifespan."""
        await self._client.aclose()
