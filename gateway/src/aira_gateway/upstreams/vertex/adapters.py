"""The two dialects Vertex serves (FRD-115 §5.1, FRD-119).

    VertexTransport            URL, OAuth, retries, Google-level errors — publisher-agnostic
    ├── VertexGeminiAdapter    Gemini bodies (reuses FRD-304's mappers unchanged)
    └── VertexAnthropicAdapter Anthropic Messages bodies

Both implement the existing ``Upstream`` protocol, so nothing above ``upstreams/`` learns that a
second vendor exists. If a change for a new vendor ever has to reach outside this package, the
canonical core is less provider-agnostic than `FRD-100` claimed — which is worth finding out here
rather than at the third vendor.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalEmbeddingRequest,
    CanonicalRequest,
    CanonicalResponse,
)
from aira_gateway.core.schema import ResponseSchema
from aira_gateway.upstreams.base import UpstreamError, UpstreamModel
from aira_gateway.upstreams.gemini_mapping import (
    SAMPLING as GEMINI_SAMPLING,
)
from aira_gateway.upstreams.gemini_mapping import (
    batch_embedding_body,
    canonical_to_gemini_embedding,
    canonical_to_gemini_request,
    embedding_values,
    gemini_chunk_to_canonical,
    gemini_response_to_canonical,
)
from aira_gateway.upstreams.vertex.anthropic_mapping import (
    SAMPLING as ANTHROPIC_SAMPLING,
)
from aira_gateway.upstreams.vertex.anthropic_mapping import (
    StreamAssembler,
    anthropic_to_canonical,
    canonical_to_anthropic,
)
from aira_gateway.upstreams.vertex.anthropic_mapping import (
    schema_refusal as anthropic_schema_refusal,
)
from aira_gateway.upstreams.vertex.transport import VertexTransport

GEMINI_METHODS = ("generateContent", "streamGenerateContent", "embedContent")
#: Anthropic has no embedding endpoint at all — the capability declaration is what refuses such a
#: request before dispatch (`FRD-114`), and the adapter never implements one.
ANTHROPIC_METHODS = ("generateContent", "streamGenerateContent")


@dataclass(frozen=True, slots=True)
class VertexModel:
    """A model this deployment reaches: where it runs, whose API it speaks, what it is called."""

    region: str
    publisher: str
    name: str

    @classmethod
    def parse(cls, spec: str) -> VertexModel:
        """``region/publisher/model`` — the three things the URL and the dialect choice need."""
        parts = spec.split("/", 2)
        if len(parts) != 3 or not all(part.strip() for part in parts):
            raise ValueError(
                f"'{spec}' is not a Vertex model spec. Expected 'region/publisher/model', "
                "e.g. 'eu/anthropic/claude-sonnet-4-5@20250929'."
            )
        return cls(parts[0].strip(), parts[1].strip(), parts[2].strip())


class VertexGeminiAdapter:
    """Google models on Vertex. Same bodies as the Generative Language API, different URL."""

    def __init__(self, transport: VertexTransport, models: list[VertexModel]) -> None:
        self._transport = transport
        self._models = {model.name: model for model in models}

    sampling_controls = GEMINI_SAMPLING
    #: Google has a schema *parameter*, so the caller's tools and a response schema are two
    #: different fields and can travel together (`FRD-131` FR-5).
    tools_with_schema = True

    def models(self) -> list[UpstreamModel]:
        return [
            UpstreamModel(m.name, m.name, GEMINI_METHODS, "vertex", m.publisher, m.region)
            for m in self._models.values()
        ]

    def _url(self, model: str, method: str) -> str:
        target = self._models[model]
        return self._transport.url(
            region=target.region, publisher=target.publisher, model=target.name, method=method
        )

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        data = await self._transport.post(
            self._url(request.model, "generateContent"), canonical_to_gemini_request(request)
        )
        return gemini_response_to_canonical(data, request.model)

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        url = f"{self._url(request.model, 'streamGenerateContent')}?alt=sse"
        async with self._transport.stream(url, canonical_to_gemini_request(request)) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    yield gemini_chunk_to_canonical(json.loads(line[len("data: ") :]))

    async def embed(self, request: CanonicalEmbeddingRequest) -> list[list[float]]:
        """A list goes to `batchEmbedContents`, a single text to `embedContent`.

        Not premature: the single-item endpoint has materially lower latency, and the overwhelming
        majority of embedding traffic is one text at a time.
        """
        if request.size > 1:
            data = await self._transport.post(
                self._url(request.model, "batchEmbedContents"),
                batch_embedding_body(request, request.model),
            )
        else:
            data = await self._transport.post(
                self._url(request.model, "embedContent"), canonical_to_gemini_embedding(request)
            )
        return embedding_values(data)


class VertexAnthropicAdapter:
    """Anthropic models on Vertex: `:rawPredict`, and a different body in both directions."""

    def __init__(
        self,
        transport: VertexTransport,
        models: list[VertexModel],
        *,
        default_max_tokens: int,
    ) -> None:
        self._transport = transport
        self._models = {model.name: model for model in models}
        # `max_tokens` is required by the API and our canonical field is optional. The per-model
        # default from the catalog (`FRD-114` FR-2) is resolved before dispatch; this is the
        # backstop for a model whose catalog entry declares none, so a caller never receives a
        # vendor error about a field they did not set.
        self._default_max_tokens = default_max_tokens

    sampling_controls = ANTHROPIC_SAMPLING
    #: A schema and the caller's tools are **separate parameters** here, as of the API checked on
    #: 2026-08-08 (`output_config.format` beside `tools`). They travel together; the model may call
    #: a function or answer with the document, and `stop_reason` says which.
    tools_with_schema = True

    @staticmethod
    def schema_refusal(schema: ResponseSchema) -> str | None:
        """This dialect's schema vocabulary is narrower than ours (`ADR-0012` §3)."""
        return anthropic_schema_refusal(schema)

    def models(self) -> list[UpstreamModel]:
        return [
            UpstreamModel(m.name, m.name, ANTHROPIC_METHODS, "vertex", m.publisher, m.region)
            for m in self._models.values()
        ]

    def _url(self, model: str, method: str) -> str:
        target = self._models[model]
        return self._transport.url(
            region=target.region, publisher=target.publisher, model=target.name, method=method
        )

    def _body(self, request: CanonicalRequest) -> dict[str, object]:
        return canonical_to_anthropic(
            request, max_tokens=request.max_output_tokens or self._default_max_tokens
        )

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        data = await self._transport.post(
            self._url(request.model, "rawPredict"), self._body(request)
        )
        # The mapper has to be told a schema was asked for: with this vendor the document arrives
        # in a tool-call block that an ordinary answer would never contain, and reading it back as
        # text is the difference between a document and prose about one.
        return anthropic_to_canonical(
            data, request.model, structured=request.response_schema is not None
        )

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        body = {**self._body(request), "stream": True}
        assembler = StreamAssembler()
        async with self._transport.stream(self._url(request.model, "streamRawPredict"), body) as r:
            async for line in r.aiter_lines():
                if not line.startswith("data: "):
                    continue
                chunk = assembler.feed(json.loads(line[len("data: ") :]))
                if chunk is not None:
                    yield chunk

    async def embed(self, request: CanonicalEmbeddingRequest) -> list[list[float]]:
        # Unreachable in the normal path: `FRD-114`'s declaration refuses an embedding request for
        # a model that does not declare the capability. This is a backstop for a misconfigured
        # catalog, not the mechanism.
        raise UpstreamError(f"Model '{request.model}' has no embedding endpoint.", 400)
