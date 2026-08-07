"""Reaching an OpenAI-compatible endpoint (FRD-123).

Everything here is about *how to get there* and nothing about the API shape — the split
`ADR-0011` asks for, and the reason `FRD-120` will add a Foundry transport beside this one rather
than a second adapter: the dialect above is already written and already tested.

What differs from `VertexTransport` is mostly what is **absent**. No credential, no project, no
region in the URL. That is not a simplification to be proud of; it is what makes a local endpoint a
development and verification tool rather than a deployment target (`FRD-123` §8).
"""

from __future__ import annotations

from typing import Any

import httpx

from aira_gateway.upstreams.base import UpstreamError

#: Ollama loads a model on its first request, which can take a minute or more for a cold one. The
#: default timeout is generous for exactly that reason — `ADR-0012` §5 says a self-deployed model
#: fails differently, and treating a cold start as an outage is the first way to get it wrong.
DEFAULT_TIMEOUT_SECONDS = 300.0


class OpenAITransport:
    """One base URL, optionally one bearer token."""

    def __init__(
        self, *, client: httpx.AsyncClient, api_key: str = "", timeout: float | None = None
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._timeout = timeout or DEFAULT_TIMEOUT_SECONDS

    async def headers(self) -> dict[str, str]:
        """The credential, if there is one.

        Async because a subclass may have to *fetch* it — an Entra token is minted and refreshed,
        not read off an attribute (`FRD-120`). A local endpoint needs none, and none is sent rather
        than a placeholder, which would look like a credential in a log.
        """
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

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
        """A read, for the readiness probe. Same credential and same error mapping as a post."""
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

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code == httpx.codes.OK:
            return
        # The status is passed through so `UPSTREAM_STATUS_MAP` can keep meaning what it means. A
        # 429 from a self-deployed endpoint means *no free replica* rather than quota (`ADR-0012`
        # §5) — the distinction belongs to whoever reads the audit, and flattening it here would
        # remove their ability to make it.
        # The provider's own reason is carried for a **400**, and only for a 400: it refused the
        # body we built and usually says exactly which field it objected to. "Upstream returned
        # 400." throws that away and leaves an operator reading a status page about a fault in
        # their own catalog.
        #
        # Not for 401/403 — those are about *our* credentials, the caller cannot act on them, and
        # the message may name the credential. Not for 5xx, which is the provider's internal noise.
        detail = _reason(response) if response.status_code == 400 else ""
        raise UpstreamError(
            f"Upstream returned {response.status_code}.{detail}", response.status_code
        )


def _reason(response: httpx.Response) -> str:
    """The provider's stated reason, if it gave one in the shape this dialect uses."""
    try:
        message = response.json().get("error", {}).get("message")
    except (ValueError, AttributeError):
        return ""
    # Bounded: an upstream is not a trusted source of arbitrarily long strings to put in our own
    # error envelope and audit log.
    return f" {str(message)[:300]}" if message else ""


class _StreamContext:
    """An async context manager over a streamed response, mirroring the Vertex transport's.

    Written out rather than borrowed from `contextlib` because the error handling has to run
    *before* the caller starts iterating: a 500 that only surfaced on the first `aiter_lines`
    would arrive after the response headers had already gone out to our own client.
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
        self._transport._raise_for_status(response)
        checked: httpx.Response = response
        return checked

    async def __aexit__(self, *exc_info: Any) -> None:
        await self._context.__aexit__(*exc_info)
