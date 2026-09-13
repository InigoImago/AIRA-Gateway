"""Reaching an OpenAI-compatible endpoint (`FRD-123`).

How to get there, and nothing about the API shape (`ADR-0011`). What differs from `VertexTransport`
is mostly what is **absent** — no credential, no project, no region in the URL — which is what makes
a local endpoint a development and verification tool rather than a deployment target (`FRD-123`
§8). A platform that authenticates subclasses this and overrides `headers`, as Foundry does.
"""

from __future__ import annotations

from typing import Any

import httpx

from aira_gateway.upstreams.base import UpstreamError, upstream_reason

#: Ollama loads a model on its first request, which can take a minute or more. A cold start is not
#: an outage (`ADR-0012` §5).
DEFAULT_TIMEOUT_SECONDS = 300.0


class OpenAITransport:
    """One base URL, and no credential of its own."""

    def __init__(self, *, client: httpx.AsyncClient, timeout: float | None = None) -> None:
        self._client = client
        self._timeout = timeout or DEFAULT_TIMEOUT_SECONDS

    async def headers(self) -> dict[str, str]:
        """No credential. Async because a subclass may have to *fetch* one (`FRD-120`)."""
        return {}

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(
                path, json=body, headers=await self.headers(), timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Upstream error: {type(exc).__name__}.") from exc
        self._raise_for_status(response)
        data: dict[str, Any] = response.json()
        return data

    async def get(self, path: str) -> dict[str, Any]:
        """A read, for listings and the readiness probe. Same credential and errors as a post."""
        try:
            response = await self._client.get(
                path, headers=await self.headers(), timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Upstream error: {type(exc).__name__}.") from exc
        self._raise_for_status(response)
        data: dict[str, Any] = response.json()
        return data

    def stream(self, path: str, body: dict[str, Any]) -> Any:
        return _StreamContext(self, path, body)

    async def aclose(self) -> None:
        """Close the connection pool. Called once, from the application lifespan."""
        await self._client.aclose()

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code == httpx.codes.OK:
            return
        # The status passes through so `UPSTREAM_STATUS_MAP` keeps its meaning: a 429 from a
        # self-deployed endpoint means *no free replica*, not quota (`ADR-0012` §5). The
        # provider's reason is carried for a 400 only (`upstream_reason`).
        detail = upstream_reason(response) if response.status_code == 400 else ""
        raise UpstreamError(
            f"Upstream returned {response.status_code}.{detail}", response.status_code
        )


class _StreamContext:
    """An async context manager over a streamed response.

    The status is judged on entry, *before* the caller iterates, so a failure never surfaces after
    our own response headers have gone out.
    """

    def __init__(self, transport: OpenAITransport, path: str, body: dict[str, Any]) -> None:
        self._transport = transport
        self._path = path
        self._body = body
        self._context: Any = None

    async def __aenter__(self) -> httpx.Response:
        self._context = self._transport._client.stream(
            "POST",
            self._path,
            json=self._body,
            headers=await self._transport.headers(),
            timeout=self._transport._timeout,
        )
        try:
            response = await self._context.__aenter__()
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Upstream error: {type(exc).__name__}.") from exc
        if response.status_code != httpx.codes.OK:
            # **Read before judging**: a streamed response has the status and not the body, and a
            # 400's reason is in the body. Only on the error path — the happy path must not buffer.
            await response.aread()
        self._transport._raise_for_status(response)
        checked: httpx.Response = response
        return checked

    async def __aexit__(self, *exc_info: Any) -> None:
        await self._context.__aexit__(*exc_info)
