"""The three dialects Vertex serves (`FRD-115` §5.1, FR-2a, `FRD-119`).

    VertexTransport            URL, credential, Google-level errors — publisher-agnostic
    ├── VertexGeminiAdapter    Gemini bodies (`gemini_mapping`, shared with Google AI Studio)
    ├── VertexAnthropicAdapter Anthropic Messages bodies (`anthropic_mapping`)
    └── VertexOpenModelAdapter OpenAI chat bodies (`openai.mapping`, shared with every
                               OpenAI-compatible server), for the managed open models

All implement the ``Upstream`` protocol, so nothing above ``upstreams/`` learns another vendor
exists, and all walk the same regional failover (`regions`).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from typing import Any

from aira_common.models import ThinkingMode
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalEmbeddingRequest,
    CanonicalRequest,
    CanonicalResponse,
    EmbeddingVectors,
    Thinking,
)
from aira_gateway.core.schema import ResponseSchema
from aira_gateway.upstreams.base import DialectUnsupported, UpstreamError, UpstreamModel
from aira_gateway.upstreams.gemini_mapping import (
    SAMPLING as GEMINI_SAMPLING,
)
from aira_gateway.upstreams.gemini_mapping import (
    canonical_to_gemini_request,
    count_tokens_body,
    gemini_chunk_to_canonical,
    gemini_response_to_canonical,
)
from aira_gateway.upstreams.openai.mapping import (
    SAMPLING as OPENAI_SAMPLING,
)
from aira_gateway.upstreams.openai.mapping import (
    StreamAssembler as OpenAIStreamAssembler,
)
from aira_gateway.upstreams.openai.mapping import (
    canonical_to_openai,
    openai_to_canonical,
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
    _declared_regions,
    _open_stream,
    _targets,
)
from aira_gateway.upstreams.vertex.transport import VertexTransport

GEMINI_METHODS = ("generateContent", "streamGenerateContent", "embedContent")
#: Anthropic and the open models have no embedding endpoint here; the capability declaration
#: refuses such a request before dispatch (`FRD-114`).
ANTHROPIC_METHODS = ("generateContent", "streamGenerateContent")
OPEN_MODEL_METHODS = ("generateContent", "streamGenerateContent")

#: Agent Platform's managed open models (`FRD-115` FR-2a), by publisher: the `chat_template_kwargs`
#: key each reads as its thinking switch — GLM thinks unless told not to, DeepSeek only when told
#: to. A publisher is listed once its answers have been read, because the open models do not agree
#: on where their thinking goes: MiniMax writes it into the answer text, which this dialect would
#: hand to a caller who never asked for reasoning.
OPEN_MODEL_PUBLISHERS = {
    "zai-org": "enable_thinking",
    "deepseek-ai": "thinking",
}

#: The probe's body: `:countTokens` of one word.
_PING_BODY = {"contents": [{"role": "user", "parts": [{"text": "ping"}]}]}

#: Opening one dialect's stream in one place — ``(region, publisher)`` in, the opened and not yet
#: read stream out (`regions._open_stream`).
StreamAttempt = Callable[[str, str], Awaitable[AsyncGenerator[CanonicalChunk]]]


class _VertexAdapter:
    """What both dialects share on Vertex: the transport, the configured models, the addressing."""

    platform_label = "Google Vertex AI"
    #: Both adapters claim `vertex`, and `serves_publisher` tells them apart — it is exactly what
    #: decides the wire format. Cataloguing a model under the pair is enough to serve it
    #: (`FRD-507`).
    serves_provider = "vertex"
    serves_publisher: str
    _methods: tuple[str, ...]
    #: The billing account's prices for a model (`FRD-509`), where one is configured. Set by
    #: `build_vertex_upstreams`; one lookup shared by every dialect on the platform.
    billing: Any = None

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

    def _place(self, model: str, addressing: dict[str, Any] | None = None) -> tuple[str, str]:
        """One place, ``(region, publisher)``, for a caller that addresses one place by
        construction (the probe).

        **The region asked about, where one is named** — even for a model `AIRA_VERTEX_MODELS`
        pins elsewhere: a probe reports a verdict per region, and contacting the configured region
        while reporting on the asked one would put one region's answer under another's name.
        Otherwise the first target. Residency is still checked, by the transport, for whichever it
        is. The request path goes through `_across_regions`, where the second region is the
        feature.
        """
        asked = _declared_regions(addressing or {})
        if asked:
            configured = self._models.get(model)
            publisher = configured.publisher if configured is not None else self.serves_publisher
            return asked[0], publisher
        return self._targets_for(model, addressing)[0]

    def _url(self, model: str, method: str, addressing: dict[str, Any] | None = None) -> str:
        """That place's URL for ``method``."""
        region, publisher = self._place(model, addressing)
        return self._transport.url(region=region, publisher=publisher, model=model, method=method)

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        """**Failover ends at the first chunk**: the chain is walked while opening, and once a chunk
        has reached the caller every later failure propagates — a second region would continue half
        an answer with a different model's first sentence."""
        attempt = self._stream_attempt(request)
        targets = self._targets_for(request.model, request.addressing)
        # Closed with this iterator: a caller that stops reading closes it, and closing it does not
        # close the stream it iterates — the response would hold its connection out of the pool
        # until the garbage collector got to it.
        async with aclosing(await _across_regions(targets, attempt)) as opened:
            async for chunk in opened:
                yield chunk

    def _stream_attempt(self, request: CanonicalRequest) -> StreamAttempt:
        """How this dialect opens ``request``'s stream in one place."""
        raise NotImplementedError

    async def embed(self, request: CanonicalEmbeddingRequest) -> list[list[float]]:
        # A backstop: `FRD-114`'s declaration refuses embedding for a model without the capability.
        raise UpstreamError(f"Model '{request.model}' has no embedding endpoint.", 400)


class VertexGeminiAdapter(_VertexAdapter):
    """Google models on Vertex. Same bodies as the Generative Language API, different URL."""

    serves_publisher = "google"
    _methods = GEMINI_METHODS
    sampling_controls = GEMINI_SAMPLING
    #: The Gemini dialect: a token budget, so every mode has a wire value.
    thinking_modes = frozenset(ThinkingMode)
    expresses_thinking_levels = True
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

    async def count_tokens(self, request: CanonicalRequest, *, region: str = "") -> int:
        """Google's own count of ``request``: `:countTokens`, which Google does not charge for and
        which counts exactly what a generation of the same body is billed as input — conversation,
        system instruction and tool declarations alike.

        Asked in ``region`` where that is a place this model is reached — the one that answered —
        so the text goes where it went before, and otherwise in the model's first place.
        """
        targets = self._targets_for(request.model, request.addressing)
        place, publisher = next(
            ((where, who) for where, who in targets if where == region), targets[0]
        )
        url = self._transport.url(
            region=place, publisher=publisher, model=request.model, method="countTokens"
        )
        data = await self._transport.post(
            url, count_tokens_body(canonical_to_gemini_request(request))
        )
        total = data.get("totalTokens")
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            raise UpstreamError("Vertex counted the request without a token total.", 502)
        return total

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

    def _stream_attempt(self, request: CanonicalRequest) -> StreamAttempt:
        body = canonical_to_gemini_request(request)

        async def attempt(region: str, publisher: str) -> AsyncGenerator[CanonicalChunk]:
            url = self._transport.url(
                region=region,
                publisher=publisher,
                model=request.model,
                method="streamGenerateContent",
            )
            return await _open_stream(
                self._transport, f"{url}?alt=sse", body, lambda: gemini_chunk_to_canonical, region
            )

        return attempt

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

    def _stream_attempt(self, request: CanonicalRequest) -> StreamAttempt:
        body = {**self._body(request), "stream": True}

        async def attempt(region: str, publisher: str) -> AsyncGenerator[CanonicalChunk]:
            url = self._transport.url(
                region=region,
                publisher=publisher,
                model=request.model,
                method="streamRawPredict",
            )
            # A fresh assembler per attempt: it accumulates tool calls across events.
            return await _open_stream(
                self._transport, url, body, lambda: StreamAssembler().feed, region
            )

        return attempt

    def _body(self, request: CanonicalRequest) -> dict[str, object]:
        return canonical_to_anthropic(
            request, max_tokens=request.max_output_tokens or self._default_max_tokens
        )


class VertexOpenModelAdapter(_VertexAdapter):
    """Agent Platform's managed open models — GLM, DeepSeek — in the OpenAI dialect (FR-2a).

    One endpoint serves every publisher, the model named in the body as ``publisher/model``; so one
    adapter per publisher, which is what the registry routes a catalogued model by (`FRD-507`).
    """

    _methods = OPEN_MODEL_METHODS
    sampling_controls = OPENAI_SAMPLING
    #: Text out only: speech is not a field of a chat completion (`FRD-624`).
    speaks = False
    #: A switch and nothing else — thinking on (the model decides how much) or off; no budget and
    #: no level word, so `limited` and every level are refused rather than approximated.
    thinking_modes = frozenset({ThinkingMode.DISABLED, ThinkingMode.AUTO})
    expresses_thinking_levels = False

    def __init__(
        self,
        transport: VertexTransport,
        models: list[VertexModel],
        *,
        publisher: str,
        thinking_switch: str,
    ) -> None:
        super().__init__(transport, models)
        self.serves_publisher = publisher
        self._thinking_switch = thinking_switch

    async def ping(self, model: str = "", addressing: dict[str, str] | None = None) -> str:
        """A question that generates nothing (`FRD-117` §5.2): an **empty conversation**.

        The endpoint looks the model up before it reads the body, so a model that is there is
        refused `400` and one that is not `404` — and neither is billed. There is no free count
        for these models (`:countTokens` answers `404`), and their listing takes no API key.
        """
        name = model or next(iter(self._models), "")
        if not name:
            return "no model configured"
        region, publisher = self._place(name, addressing)
        url = self._transport.open_models_url(region=region, model=name)
        try:
            await self._transport.post(url, {"model": f"{publisher}/{name}", "messages": []})
        except UpstreamError as exc:
            if exc.status_code != 400:
                raise
        return f"{name} answered"

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        body = self._body(request)

        async def attempt(region: str, publisher: str) -> CanonicalResponse:
            url = self._transport.open_models_url(region=region, model=request.model)
            data = await self._transport.post(url, _named(body, publisher, request.model))
            # The use case's switch, carried to the mapper: these models send their thinking
            # whether or not anybody asked (`FRD-135` FR-3).
            answer = openai_to_canonical(
                data, request.model, include_reasoning=request.include_reasoning
            )
            return answer.model_copy(update={"served_region": region})

        return await _across_regions(self._targets_for(request.model, request.addressing), attempt)

    def _stream_attempt(self, request: CanonicalRequest) -> StreamAttempt:
        body = self._body(request, stream=True)

        async def attempt(region: str, publisher: str) -> AsyncGenerator[CanonicalChunk]:
            url = self._transport.open_models_url(region=region, model=request.model)
            # A fresh assembler per attempt: it accumulates tool calls across events.
            return await _open_stream(
                self._transport,
                url,
                _named(body, publisher, request.model),
                lambda: OpenAIStreamAssembler().feed,
                region,
            )

        return attempt

    def _body(self, request: CanonicalRequest, *, stream: bool = False) -> dict[str, Any]:
        """The dialect's body, with thinking said the way these models read it: a template switch
        rather than `reasoning_effort`, which no model here documents."""
        body = canonical_to_openai(request.model_copy(update={"thinking": None}), stream=stream)
        if request.thinking is not None:
            body["chat_template_kwargs"] = {self._thinking_switch: _thinks(request.thinking)}
        return body


def _named(body: dict[str, Any], publisher: str, model: str) -> dict[str, Any]:
    """The body naming the model as this endpoint knows it: ``publisher/model``. A caller names the
    model alone, and the publisher is the place's — which is where it can differ."""
    return {**body, "model": f"{publisher}/{model}"}


def _thinks(setting: Thinking) -> bool:
    """The switch's value: off for `disabled`, on for `auto`.

    A backstop for anything else — the chain skips a candidate whose dialect cannot express the
    mode (`thinking_modes`, `expresses_thinking_levels`), so reaching it means a declaration claims
    what this wire cannot say, and that must not degrade to a guess.
    """
    if setting.mode == ThinkingMode.DISABLED:
        return False
    if setting.mode == ThinkingMode.AUTO:
        return True
    raise DialectUnsupported(
        f"These models take thinking on or off, not '{setting.mode}'. It is refused rather than "
        "rounded to one of the two: either would spend a different amount than was asked for."
    )
