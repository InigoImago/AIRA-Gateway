"""An upstream that speaks the OpenAI wire format (FRD-123).

    OpenAITransport            base URL, optional bearer, errors
    └── OpenAIAdapter          /v1/chat/completions, /v1/embeddings   ← this file

Implements the same ``Upstream`` protocol as the mock, the Generative Language adapter and the two
Vertex adapters, so nothing above ``upstreams/`` learns that a third dialect exists. The
architecture assertion in ``test_vertex.py`` checks that claim by parsing every module outside the
adapter packages; a change here that reached beyond them would fail it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalEmbeddingRequest,
    CanonicalRequest,
    CanonicalResponse,
)
from aira_gateway.upstreams.base import UpstreamModel
from aira_gateway.upstreams.openai.mapping import (
    SAMPLING as OPENAI_SAMPLING,
)
from aira_gateway.upstreams.openai.mapping import (
    canonical_to_openai,
    canonical_to_openai_embedding,
    embedding_values,
    openai_chunk_to_canonical,
    openai_to_canonical,
    parse_sse_line,
)
from aira_gateway.upstreams.openai.routes import Routes, StandardRoutes
from aira_gateway.upstreams.openai.transport import OpenAITransport

CHAT_METHODS = ("generateContent", "streamGenerateContent")
EMBED_METHODS = ("embedContent", "batchEmbedContents")


class OpenAIAdapter:
    """Models reached through an OpenAI-compatible endpoint.

    ``embedding_models`` is separate from ``models`` because the two verb sets are disjoint here:
    a chat model has no embedding endpoint and an embedding model has no chat endpoint, and
    advertising both for everything would make `FRD-114`'s capability declaration the only thing
    standing between a caller and a vendor error. The registry's method list should tell the truth.
    """

    def __init__(
        self,
        transport: OpenAITransport,
        models: list[str],
        *,
        embedding_models: list[str] | None = None,
        provider: str = "openai-compatible",
        publisher: str = "",
        region: str = "",
        routes: Routes | None = None,
    ) -> None:
        self._transport = transport
        # How this platform addresses a model (`ADR-0011`'s third axis). The default is the plain
        # form; Azure puts the deployment in the path and omits the model from the body.
        self._routes: Routes = routes or StandardRoutes()
        self._provider = provider
        self._publisher = publisher
        # Declared rather than left empty even for a local endpoint: a self-hosted model is the
        # strongest residency story there is, and an audit row that records nothing cannot say so
        # (`FRD-123` §5.3).
        self._region = region
        self._chat = list(models)
        self._embedding = list(embedding_models or [])

    sampling_controls = OPENAI_SAMPLING

    def models(self) -> list[UpstreamModel]:
        return [
            UpstreamModel(name, name, CHAT_METHODS, self._provider, self._publisher, self._region)
            for name in self._chat
        ] + [
            UpstreamModel(name, name, EMBED_METHODS, self._provider, self._publisher, self._region)
            for name in self._embedding
        ]

    def _named(self, body: dict[str, Any], model: str) -> dict[str, Any]:
        """Put the model field the *platform* wants into a body the dialect already wrote.

        The dialect always writes one; a platform that addresses by path takes it back out. Doing
        it here rather than in the mapper is what keeps the dialect platform-free, which is the
        property `FRD-120` §5.1 depends on to reuse it unchanged.
        """
        named = self._routes.body_model(model)
        if named is None:
            body.pop("model", None)
        else:
            body["model"] = named
        return body

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        body = self._named(canonical_to_openai(request), request.model)
        data = await self._transport.post(self._routes.chat(request.model), body)
        return openai_to_canonical(data, request.model)

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        body = self._named(canonical_to_openai(request, stream=True), request.model)
        async with self._transport.stream(self._routes.chat(request.model), body) as response:
            async for line in response.aiter_lines():
                payload = parse_sse_line(line)
                if payload is None:
                    continue
                chunk = openai_chunk_to_canonical(payload)
                if chunk is not None:
                    yield chunk

    @property
    def probe_name(self) -> str:
        """How this adapter appears in `/readyz`. The configured name, not the class — three
        servers of the same kind must be distinguishable, which is the whole point of naming
        them (`FRD-123`)."""
        return self._provider

    async def ping(self) -> str:
        """The cheapest remote question there is (`FRD-117` §5.2).

        A **GET of a listing**, never a generation: a probe that generated would cost money to
        answer "are you there", and against a self-deployed endpoint it would wake a scaled-to-zero
        model on every health check.
        """
        listing = await self._transport.get(self._routes.listing())
        count = len(listing.get("data") or [])
        return f"{count} model(s) listed" if count else "endpoint answered"

    async def embed(self, request: CanonicalEmbeddingRequest) -> list[list[float]]:
        body = self._named(canonical_to_openai_embedding(request), request.model)
        data = await self._transport.post(self._routes.embed(request.model), body)
        return embedding_values(data)
