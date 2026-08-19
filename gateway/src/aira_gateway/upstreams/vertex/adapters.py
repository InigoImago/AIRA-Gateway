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

from aira_common.models import ThinkingMode
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalEmbeddingRequest,
    CanonicalRequest,
    CanonicalResponse,
)
from aira_gateway.core.schema import ResponseSchema
from aira_gateway.upstreams.base import AmbiguousModel, UpstreamError, UpstreamModel
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


def _target(
    models: dict[str, VertexModel],
    publisher: str,
    model: str,
    addressing: dict[str, str],
) -> tuple[str, str]:
    """Where to send a request for `model`: its `(region, publisher)`.

    The configured entry first — a deployment that named the model in `AIRA_VERTEX_MODELS` keeps
    working exactly as before. Otherwise the **catalogue's** addressing, which is what makes
    cataloguing a Vertex model enough to serve it: this platform's name is not its whole address,
    so the region has to come from somewhere, and it comes from the entry an administrator filled
    in rather than from a second list in the environment.

    **Residency still holds for a catalogued model**, and nothing new was needed for it: the
    transport checks the region inside `url()`, on every call. A configured model is checked at
    startup as well, and a catalogued one arrives afterwards over Kafka — so the check that covers
    both is the one at the moment of addressing, which was already there. Worth stating because
    the obvious move was to add a second check here, and two owners of one rule is how they
    disagree.

    A model with neither a configured entry nor a region is refused by name: the alternative is
    guessing a region, and a guess about residency is the one guess this product may not make.
    """
    configured = models.get(model)
    if configured is not None:
        return configured.region, configured.publisher
    region = (addressing or {}).get("region", "").strip()
    if not region:
        raise AmbiguousModel(
            f"'{model}' is catalogued for this platform and says no region. Vertex addresses a "
            "model by region, so there is nothing to send it to — set the region on the model in "
            "the catalogue, or name it in AIRA_VERTEX_MODELS."
        )
    return region, publisher


class VertexGeminiAdapter:
    """Google models on Vertex. Same bodies as the Generative Language API, different URL."""

    platform_label = "Google Vertex AI"

    def __init__(self, transport: VertexTransport, models: list[VertexModel]) -> None:
        self._transport = transport
        self._models = {model.name: model for model in models}

    sampling_controls = GEMINI_SAMPLING
    #: The Gemini dialect over Vertex: a token budget, so every mode has a wire value.
    thinking_modes = frozenset(ThinkingMode)
    #: Cataloguing a model for this provider **and** this publisher is enough to serve it.
    #:
    #: Two adapters share the provider name `vertex` — this one speaks Gemini, the other speaks
    #: Anthropic — so neither could claim it alone, and a Vertex model catalogued through the
    #: console was an entry that would never answer. The publisher is the discriminator the
    #: catalogue already carries, and it is exactly the thing that decides the wire format.
    serves_provider = "vertex"
    serves_publisher = "google"
    #: Google has a schema *parameter*, so the caller's tools and a response schema are two
    #: different fields and can travel together (`FRD-131` FR-5).
    tools_with_schema = True

    def models(self) -> list[UpstreamModel]:
        return [
            UpstreamModel(m.name, m.name, GEMINI_METHODS, "vertex", m.publisher, m.region)
            for m in self._models.values()
        ]

    def _url(self, model: str, method: str, addressing: dict[str, str] | None = None) -> str:
        region, publisher = _target(self._models, self.serves_publisher, model, addressing or {})
        return self._transport.url(region=region, publisher=publisher, model=model, method=method)

    async def ping(self, model: str = "", addressing: dict[str, str] | None = None) -> str:
        """The cheapest remote question this platform has (`FRD-117` §5.2).

        `:countTokens`, which **Google does not charge for** — measured on 2026-08-17, and it is
        the reason this exists at all. Vertex publishes no listing an API key may read (its
        `/publishers/google/models` answers *"API keys are not supported by this API"*), so this
        adapter had **no probe**: `/readyz` reported it as *"no probe available; not checked"* and
        the console's *Check reachability* answered "Served — not contacted" for every model on it.
        Honest, and useless — the operator asked whether their credential works and was told
        nobody had looked.

        A generation would have been the obvious alternative and is the wrong one: a probe that
        generates costs money to answer "are you there", every time anything asks.
        """
        # **The model that was asked about**, where the caller says which one.
        #
        # This picked whichever model happened to be configured first, so the check reported
        # *"gemini-2.5-flash answered"* to somebody asking about `gemini-2.5-pro` — an answer about
        # the credential, worded as an answer about the model. Since cataloguing became enough to
        # serve a model that is the common case rather than an edge: the model being checked is
        # usually the one *not* in configuration, and it is the one an administrator has just typed
        # and wants to know about.
        #
        # `:countTokens` is free (measured 2026-08-17), so asking about the real model costs
        # nothing and answers more: it is how `gemini-3.5-flash` was found not to exist on this
        # platform at all, where a ping of some other model would have said everything was fine.
        if model:
            await self._transport.post(
                self._url(model, "countTokens", addressing),
                {"contents": [{"role": "user", "parts": [{"text": "ping"}]}]},
            )
            return f"{model} answered"
        if not self._models:
            return "no model configured"
        name = next(iter(self._models))
        await self._transport.post(
            self._url(name, "countTokens"),
            {"contents": [{"role": "user", "parts": [{"text": "ping"}]}]},
        )
        return f"{name} answered"

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        data = await self._transport.post(
            self._url(request.model, "generateContent", request.addressing),
            canonical_to_gemini_request(request),
        )
        return gemini_response_to_canonical(data, request.model)

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        url = f"{self._url(request.model, 'streamGenerateContent', request.addressing)}?alt=sse"
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
                self._url(request.model, "batchEmbedContents", request.addressing),
                batch_embedding_body(request, request.model),
            )
        else:
            data = await self._transport.post(
                self._url(request.model, "embedContent", request.addressing),
                canonical_to_gemini_embedding(request),
            )
        return embedding_values(data)

    async def aclose(self) -> None:
        """Close the connection pool this adapter owns (`ProviderRegistry.aclose`)."""
        await self._transport.aclose()


class VertexAnthropicAdapter:
    """Anthropic models on Vertex: `:rawPredict`, and a different body in both directions."""

    platform_label = "Google Vertex AI"

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
    #: The other half of the pair — see `VertexGeminiAdapter.serves_provider`.
    serves_provider = "vertex"
    serves_publisher = "anthropic"
    #: **No `auto`.** Anthropic takes `budget_tokens` and has no "decide for yourself" value, so
    #: `FRD-111` §5.2 resolves that mode to the model's declared default budget — and where no
    #: default is declared there is nothing to send. That case used to omit the block entirely,
    #: which is a caller asking for thinking, receiving none, and being told nothing.
    thinking_modes = frozenset(ThinkingMode) - {ThinkingMode.AUTO}
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

    def _url(self, model: str, method: str, addressing: dict[str, str] | None = None) -> str:
        region, publisher = _target(self._models, self.serves_publisher, model, addressing or {})
        return self._transport.url(region=region, publisher=publisher, model=model, method=method)

    def _body(self, request: CanonicalRequest) -> dict[str, object]:
        return canonical_to_anthropic(
            request, max_tokens=request.max_output_tokens or self._default_max_tokens
        )

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        data = await self._transport.post(
            self._url(request.model, "rawPredict", request.addressing), self._body(request)
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
        url = self._url(request.model, "streamRawPredict", request.addressing)
        async with self._transport.stream(url, body) as r:
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

    async def aclose(self) -> None:
        """Close the connection pool this adapter owns (`ProviderRegistry.aclose`)."""
        await self._transport.aclose()
