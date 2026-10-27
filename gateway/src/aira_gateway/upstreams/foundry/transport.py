"""Reaching an Azure OpenAI resource: its credential, over the dialect's transport (`FRD-120`)."""

from __future__ import annotations

import httpx

from aira_common.logging import get_logger
from aira_common.tokens import TokenSource, TokenUnavailable
from aira_gateway.upstreams.base import UpstreamError
from aira_gateway.upstreams.openai.transport import OpenAITransport

#: What a caller is told when this deployment's Entra credential fails. The cause is the operator's
#: to fix and goes to the log; an API caller can do nothing with it.
CREDENTIALS_UNAVAILABLE = (
    "Azure credentials are unavailable, so the request could not be sent. The gateway's log has "
    "the cause for its operator."
)

_log = get_logger("aira_gateway.upstreams")


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
        super().__init__(client=client, timeout=timeout, name="Microsoft Foundry")
        self._azure_key = api_key
        self._tokens = tokens

    async def headers(self) -> dict[str, str]:
        # Azure's key goes in `api-key`, **not** `Authorization`: sent as a bearer it produces a 401
        # that says nothing about which of the two was wrong.
        if self._azure_key:
            return {"api-key": self._azure_key}
        if self._tokens is not None:
            try:
                token = await self._tokens.token()
            except TokenUnavailable as exc:
                # An **upstream** failure (503), as on Vertex: the caller did nothing wrong, and an
                # unhandled exception here would be a 500 blaming the gateway.
                _log.error("foundry_credentials_unavailable", error=str(exc))
                raise UpstreamError(CREDENTIALS_UNAVAILABLE, 503) from exc
            return {"Authorization": f"Bearer {token}"}
        return {}
