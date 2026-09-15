"""The two dialects Vertex serves (`FRD-115` §5.1, `FRD-119`).

    VertexTransport            URL, credential, Google-level errors — publisher-agnostic
    ├── VertexGeminiAdapter    Gemini bodies (`gemini_mapping`, shared with Google AI Studio)
    └── VertexAnthropicAdapter Anthropic Messages bodies (`anthropic_mapping`)

Both implement the ``Upstream`` protocol, so nothing above ``upstreams/`` learns a second vendor
exists, and both walk the same regional failover (`regions`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from aira_common.models import ThinkingMode
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalEmbeddingRequest,
    CanonicalRequest,
    CanonicalResponse,
    EmbeddingVectors,
)
from aira_gateway.core.schema import ResponseSchema
from aira_gateway.upstreams.base import UpstreamError, UpstreamModel
from aira_gateway.upstreams.gemini_mapping import (
    SAMPLING as GEMINI_SAMPLING,
)
from aira_gateway.upstreams.gemini_mapping import (
    canonical_to_gemini_request,
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
from aira_gateway.upstreams.vertex.embedding_mapping import (
    predict_body,
    predict_tokens,
    predict_values,
)
from aira_gateway.upstreams.vertex.regions import (
    VertexModel,
    _across_regions,
    _open_stream,
    _targets,
)
from aira_gateway.upstreams.vertex.transport import VertexTransport

GEMINI_METHODS = ("generateContent", "streamGenerateContent", "embedContent")
#: Anthropic has no embedding endpoint; the capability declaration refuses such a request before
#: dispatch (`FRD-114`).
ANTHROPIC_METHODS = ("generateContent", "streamGenerateContent")

#: The probe's body: `:countTokens` of one word.
_PING_BODY = {"contents": [{"role": "user", "parts": [{"text": "ping"}]}]}


class _VertexAdapter:
    """What both dialects share on Vertex: the transport, the configured models, the addressing."""

    platform_label = "Google Vertex AI"
    #: Both adapters claim `vertex`, and `serves_publisher` tells them apart — it is exactly what
    #: decides the wire format. Cataloguing a model under the pair is enough to serve it
    #: (`FRD-507`).
    serves_provider = "vertex"
    serves_publisher: str
    _methods: tuple[str, ...]

    def __init__(self, transport: VertexTransport, models: list[VertexModel]) -> None:
        self._transport = transport
        self._models = {model.name: model for model in models}

    def models(self) -> list[UpstreamModel]:
        return [
            UpstreamModel(m.name, m.name, self._methods, "vertex", m.publisher, m.region)
            for m in self._models.values()
        ]

    async def aclose(self) -> None:
        """Close the connection pool this adapter owns (`ProviderRegistry.aclose`)."""
        await self._transport.aclose()

    def _targets_for(
        self, model: str, addressing: dict[str, Any] | None
    ) -> tuple[tuple[str, str], ...]:
        return _targets(self._models, self.serves_publisher, model, addressing or {})

    def _url(self, model: str, method: str, addressing: dict[str, Any] | None = None) -> str:
        """The **first** target's URL, for a caller that addresses one place by construction (the
        probe). The request path goes through `_across_regions`, where the second region is the
        feature."""
        region, publisher = self._targets_for(model, addressing)[0]
        return self._transport.url(region=region, publisher=publisher, model=model, method=method)


class VertexGeminiAdapter(_VertexAdapter):
    """Google models on Vertex. Same bodies as the Generative Language API, different URL."""

    serves_publisher = "google"
    _methods = GEMINI_METHODS
    sampling_controls = GEMINI_SAMPLING
    #: The Gemini dialect: a token budget, so every mode has a wire value.
    thinking_modes = frozenset(ThinkingMode)
    expresses_thinking_levels = True
    #: A schema *parameter*, so the caller's tools and a response schema travel together
    #: (`FRD-131` FR-5).
    tools_with_schema = True
    #: The same wire as Google AI Studio, which carries `responseModalities` and a voice
    #: (`FRD-624`).
    speaks = True

    async def ping(self, model: str = "", addressing: dict[str, str] | None = None) -> str:
        """The cheapest remote question this platform has (`FRD-117` §5.2): `:countTokens`.

        Google does not charge for it, and Vertex publishes no listing an API key may read. The
        model asked about is the one probed — usually the one just catalogued — so an answer about
        the credential is never worded as an answer about a model.
        """
        if model:
            await self._transport.post(self._url(model, "countTokens", addressing), _PING_BODY)
            return f"{model} answered"
        if not self._models:
            return "no model configured"
        name = next(iter(self._models))
        await self._transport.post(
            self._url(name, "countTokens"),
            _PING_BODY,
        )
        return f"{name} answered"

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        body = canonical_to_gemini_request(request)

        async def attempt(region: str, publisher: str) -> CanonicalResponse:
            url = self._transport.url(
                region=region, publisher=publisher, model=request.model, method="generateContent"
            )
            data = await self._transport.post(url, body)
            answer = gemini_response_to_canonical(data, request.model)
            # The region that answered, not the catalogue's first: the audit row's residency claim
            # must name where the request went (`FRD-115` FR-10).
            return answer.model_copy(update={"served_region": region})

        return await _across_regions(self._targets_for(request.model, request.addressing), attempt)

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        """**Failover ends at the first chunk**: the chain is walked while opening, and once a chunk
        has reached the caller every later failure propagates — a second region would continue half
        an answer with a different model's first sentence."""
        body = canonical_to_gemini_request(request)

        async def attempt(region: str, publisher: str) -> AsyncIterator[CanonicalChunk]:
            url = self._transport.url(
                region=region,
                publisher=publisher,
                model=request.model,
                method="streamGenerateContent",
            )
            return await _open_stream(
                self._transport, f"{url}?alt=sse", body, lambda: gemini_chunk_to_canonical
            )

        targets = self._targets_for(request.model, request.addressing)
        opened = await _across_regions(targets, attempt)
        async for chunk in opened:
            yield chunk

    async def embed(self, request: CanonicalEmbeddingRequest) -> list[list[float]]:
        """Through `:predict`, one text per call, in the caller's order (`embedding_mapping`).

        Embeddings take the regional chain too: a batch refused for want of quota is exactly what
        failover is for.
        """

        async def attempt(region: str, publisher: str) -> EmbeddingVectors:
            url = self._transport.url(
                region=region, publisher=publisher, model=request.model, method="predict"
            )
            answers = [
                await self._transport.post(url, predict_body(request, text))
                for text in request.texts
            ]
            counts = [predict_tokens(answer) for answer in answers]
            # Where it was answered and what it cost, for the audit row and the price.
            return EmbeddingVectors(
                (predict_values(answer) for answer in answers),
                served_region=region,
                input_tokens=None if None in counts else sum(count or 0 for count in counts),
            )

        return await _across_regions(self._targets_for(request.model, request.addressing), attempt)


class VertexAnthropicAdapter(_VertexAdapter):
    """Anthropic models on Vertex: `:rawPredict`, and a different body in both directions."""

    serves_publisher = "anthropic"
    _methods = ANTHROPIC_METHODS
    sampling_controls = ANTHROPIC_SAMPLING
    #: Anthropic's API has no speech output (`FRD-624`).
    speaks = False
    #: **No `auto`**: this dialect takes only `budget_tokens`, so `auto` resolves to the model's
    #: declared default budget (`FRD-111` §5.2), and without one there is nothing to send.
    thinking_modes = frozenset(ThinkingMode) - {ThinkingMode.AUTO}
    #: No field for a level word: it has nowhere to go and is refused rather than dropped.
    expresses_thinking_levels = False
    #: `output_config.format` sits beside `tools`, so a schema and the caller's tools travel
    #: together and `stop_reason` says which one the model answered with.
    tools_with_schema = True

    def __init__(
        self,
        transport: VertexTransport,
        models: list[VertexModel],
        *,
        default_max_tokens: int,
    ) -> None:
        super().__init__(transport, models)
        # `max_tokens` is required by this API. The catalog's per-model default is resolved before
        # dispatch (`FRD-114` FR-2); this is the backstop for a model that declares none.
        self._default_max_tokens = default_max_tokens

    @staticmethod
    def schema_refusal(schema: ResponseSchema) -> str | None:
        """This dialect's schema vocabulary is narrower than ours (`ADR-0012` §3)."""
        return anthropic_schema_refusal(schema)

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        body = self._body(request)

        async def attempt(region: str, publisher: str) -> CanonicalResponse:
            url = self._transport.url(
                region=region, publisher=publisher, model=request.model, method="rawPredict"
            )
            data = await self._transport.post(url, body)
            # Told whether a schema was asked for, so an answer that is not the document is
            # refused as `SCHEMA_UNSATISFIED` rather than returned as prose.
            answer = anthropic_to_canonical(
                data,
                request.model,
                structured=request.response_schema is not None,
                # The use case's switch, carried rather than decided here (`FRD-135` FR-3).
                include_reasoning=request.include_reasoning,
            )
            return answer.model_copy(update={"served_region": region})

        return await _across_regions(self._targets_for(request.model, request.addressing), attempt)

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        """The same boundary as the Gemini adapter's: a stream that has sent a byte is committed."""
        body = {**self._body(request), "stream": True}

        async def attempt(region: str, publisher: str) -> AsyncIterator[CanonicalChunk]:
            url = self._transport.url(
                region=region,
                publisher=publisher,
                model=request.model,
                method="streamRawPredict",
            )
            # A fresh assembler per attempt: it accumulates tool calls across events.
            return await _open_stream(self._transport, url, body, lambda: StreamAssembler().feed)

        targets = self._targets_for(request.model, request.addressing)
        opened = await _across_regions(targets, attempt)
        async for chunk in opened:
            yield chunk

    async def embed(self, request: CanonicalEmbeddingRequest) -> list[list[float]]:
        # A backstop: `FRD-114`'s declaration refuses embedding for a model without the capability.
        raise UpstreamError(f"Model '{request.model}' has no embedding endpoint.", 400)

    def _body(self, request: CanonicalRequest) -> dict[str, object]:
        return canonical_to_anthropic(
            request, max_tokens=request.max_output_tokens or self._default_max_tokens
        )
