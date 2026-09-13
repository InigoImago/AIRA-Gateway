"""Reaching an Azure OpenAI resource: its credential, over the dialect's transport (`FRD-120`)."""

from __future__ import annotations

import httpx

from aira_common.tokens import TokenSource
from aira_gateway.upstreams.openai.transport import OpenAITransport


class FoundryTransport(OpenAITransport):
    """Azure's credential, over the OpenAI dialect's own transport.

    Subclassed because everything about *sending* is already right — the streamed-error ordering,
    the status pass-through that keeps a 429 meaning capacity. What differs is one header. Two
    credentials: an **API key**, what a developer has on day one, and **Entra** (a bearer token from
    the shared :class:`TokenSource`), what an organisation with a key-rotation policy deploys.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        api_key: str = "",
        tokens: TokenSource | None = None,
        timeout: float | None = None,
    ) -> None:
        super().__init__(client=client, timeout=timeout)
        self._azure_key = api_key
        self._tokens = tokens

    async def headers(self) -> dict[str, str]:
        # Azure's key goes in `api-key`, **not** `Authorization`: sent as a bearer it produces a 401
        # that says nothing about which of the two was wrong.
        if self._azure_key:
            return {"api-key": self._azure_key}
        if self._tokens is not None:
            return {"Authorization": f"Bearer {await self._tokens.token()}"}
        return {}
