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

from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalEmbeddingRequest,
    CanonicalRequest,
    CanonicalResponse,
)
from aira_gateway.upstreams.base import UpstreamModel
from aira_gateway.upstreams.openai.mapping import (
    canonical_to_openai,
    canonical_to_openai_embedding,
    embedding_values,
    openai_chunk_to_canonical,
    openai_to_canonical,
    parse_sse_line,
)
from aira_gateway.upstreams.openai.transport import OpenAITransport

CHAT_METHODS = ("generateContent", "streamGenerateContent")
EMBED_METHODS = ("embedContent", "batchEmbedContents")

CHAT_PATH = "/v1/chat/completions"
EMBED_PATH = "/v1/embeddings"


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
    ) -> None:
        self._transport = transport
        self._provider = provider
        self._publisher = publisher
        # Declared rather than left empty even for a local endpoint: a self-hosted model is the
        # strongest residency story there is, and an audit row that records nothing cannot say so
        # (`FRD-123` §5.3).
        self._region = region
        self._chat = list(models)
        self._embedding = list(embedding_models or [])

    def models(self) -> list[UpstreamModel]:
        return [
            UpstreamModel(name, name, CHAT_METHODS, self._provider, self._publisher, self._region)
            for name in self._chat
        ] + [
            UpstreamModel(name, name, EMBED_METHODS, self._provider, self._publisher, self._region)
            for name in self._embedding
        ]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        data = await self._transport.post(CHAT_PATH, canonical_to_openai(request))
        return openai_to_canonical(data, request.model)

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        body = canonical_to_openai(request, stream=True)
        async with self._transport.stream(CHAT_PATH, body) as response:
            async for line in response.aiter_lines():
                payload = parse_sse_line(line)
                if payload is None:
                    continue
                chunk = openai_chunk_to_canonical(payload)
                if chunk is not None:
                    yield chunk

    async def embed(self, request: CanonicalEmbeddingRequest) -> list[list[float]]:
        data = await self._transport.post(EMBED_PATH, canonical_to_openai_embedding(request))
        return embedding_values(data)
