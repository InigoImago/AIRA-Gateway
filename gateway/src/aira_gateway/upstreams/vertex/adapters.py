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
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aira_common.models import ThinkingMode
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalEmbeddingRequest,
    CanonicalRequest,
    CanonicalResponse,
)
from aira_gateway.core.schema import ResponseSchema
from aira_gateway.residency import RegionNotAllowed
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


#: Upstream statuses that mean **try the next region**, and nothing else (`FRD-609`).
#:
#: The distinction is the whole feature. A `404` says the model is not in *this* region, a `429`
#: says this region has no quota left right now, a `5xx` says this region is unwell — three facts
#: about a **place**, and somewhere else may answer. A `400` says the request is malformed and a
#: `401`/`403` says the credential is wrong: facts about the **request**, identical in every
#: region, and retrying them would spend three times as long arriving at the same refusal while
#: the caller waits.
#:
#: Not in here on purpose: a model that answers and refuses on content. That is the model's
#: answer, it arrives as a `200`, and asking a second region for a nicer one is shopping for a
#: verdict.
REGION_FAILOVER_STATUSES = frozenset({404, 408, 429, 500, 502, 503, 504})


def _targets(
    models: dict[str, VertexModel],
    publisher: str,
    model: str,
    addressing: dict[str, Any],
) -> tuple[tuple[str, str], ...]:
    """Every `(region, publisher)` this model may be tried at, **in order** (`FRD-609`).

    A list rather than one pair because a model may live in several regions and a region can fail
    for reasons that are about the region: no quota here, not deployed here, unwell here. The
    order is the catalogue's, and it is a preference — the first entry is what an ordinary request
    uses, and the rest exist for the moments when it cannot.

    Residency is **not** filtered here. Every candidate is checked by `url()` at the moment it is
    addressed, exactly as before, and a region this installation does not permit raises
    `RegionNotAllowed` — which the caller below treats as *this one is unavailable*, moving on. One
    owner for the residency rule, and the failover loop learns the answer by asking it rather than
    by keeping a copy of the allow-list.

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
        # A deployment that named the model in `AIRA_VERTEX_MODELS` keeps working exactly as
        # before, one region and no chain: that list has one entry per model by construction.
        return ((configured.region, configured.publisher),)
    regions = _declared_regions(addressing)
    if not regions:
        raise AmbiguousModel(
            f"'{model}' is catalogued for this platform and says no region. Vertex addresses a "
            "model by region, so there is nothing to send it to — set the region on the model in "
            "the catalogue, or name it in AIRA_VERTEX_MODELS."
        )
    return tuple((region, publisher) for region in regions)


def _declared_regions(addressing: dict[str, Any]) -> tuple[str, ...]:
    """The catalogue's regions, both spellings, in order.

    The same normalisation `ModelDeclaration.regions` does, and deliberately duplicated **here
    rather than imported**: this module is the upstream layer and `catalog.py` is the read-model,
    and an adapter reaching into the catalogue for a shape would be the dependency `ADR-0011` keeps
    out. What is shared is the *format*, which `test_the_two_readers_of_a_region_list_agree` pins.
    """
    block = addressing or {}
    raw = block.get("regions")
    if raw is None:
        single = block.get("region")
        raw = [single] if isinstance(single, str) else []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    seen: dict[str, None] = {}
    for region in raw:
        if isinstance(region, str) and region.strip():
            seen.setdefault(region.strip(), None)
    return tuple(seen)


async def _open_stream(
    transport: VertexTransport, url: str, body: dict[str, Any]
) -> AsyncIterator[CanonicalChunk]:
    """Open a streamed call and return an iterator over its chunks.

    Split from the iteration on purpose, and the split **is** the failover boundary: everything
    that can go wrong about a *region* — not deployed, no quota, unwell — goes wrong while opening,
    where `_raise_for_status` runs and no byte has reached the caller yet. After this function
    returns, the stream is committed.

    The context is entered here and closed by the generator's `finally`, so it outlives this call
    by exactly the length of the iteration.
    """
    context = transport.stream(url, body)
    response = await context.__aenter__()

    async def chunks() -> AsyncIterator[CanonicalChunk]:
        try:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    yield gemini_chunk_to_canonical(json.loads(line[len("data: ") :]))
        finally:
            await context.__aexit__(None, None, None)

    return chunks()


async def _open_assembled_stream(
    transport: VertexTransport, url: str, body: dict[str, Any]
) -> AsyncIterator[CanonicalChunk]:
    """`_open_stream` for the Anthropic dialect, whose deltas need assembling (`FRD-119`).

    The assembler is created **per attempt** rather than shared across the chain: it accumulates a
    tool call across several events, and a half-assembled call carried into a second region would
    be completed with fragments from a different response.
    """
    context = transport.stream(url, body)
    response = await context.__aenter__()

    async def chunks() -> AsyncIterator[CanonicalChunk]:
        assembler = StreamAssembler()
        try:
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                chunk = assembler.feed(json.loads(line[len("data: ") :]))
                if chunk is not None:
                    yield chunk
        finally:
            await context.__aexit__(None, None, None)

    return chunks()


async def _across_regions[T](
    targets: tuple[tuple[str, str], ...],
    attempt: Callable[[str, str], Awaitable[T]],
) -> T:
    """Try each region in order, moving on only for a failure that is about the **place**.

    Three kinds of answer, and only one of them means *ask somewhere else*:

    - `RegionNotAllowed` — this installation's residency policy does not permit the region. Not an
      error about the model at all, and the reason the failover loop keeps **no copy of the
      allow-list**: it learns the answer by addressing the region and being told, so residency
      still has exactly one owner (`transport.url`).
    - an upstream status in `REGION_FAILOVER_STATUSES` — not deployed here, no quota here, unwell
      here. Somewhere else may answer.
    - anything else — a malformed request, a bad credential, a model that answered. Identical in
      every region, so retrying spends the caller's time arriving at the same refusal.

    The **last** failure is raised when every region is exhausted, not the first: a caller reading
    *"429 in europe-west4"* learns that the chain was tried and where it ended, where the first
    would suggest nothing was tried at all.
    """
    last: Exception | None = None
    for region, publisher in targets:
        try:
            return await attempt(region, publisher)
        except RegionNotAllowed as exc:
            last = exc
        except UpstreamError as exc:
            if exc.status_code not in REGION_FAILOVER_STATUSES:
                raise
            last = exc
    if last is not None:
        raise last
    # Unreachable through `_targets`, which refuses an empty list by name. Stated rather than
    # assumed: a loop whose only exit is a raise is one nobody can read without checking.
    raise AmbiguousModel("No region was available to address this model.")


class VertexGeminiAdapter:
    """Google models on Vertex. Same bodies as the Generative Language API, different URL."""

    platform_label = "Google Vertex AI"

    def __init__(self, transport: VertexTransport, models: list[VertexModel]) -> None:
        self._transport = transport
        self._models = {model.name: model for model in models}

    sampling_controls = GEMINI_SAMPLING
    #: The Gemini dialect over Vertex: a token budget, so every mode has a wire value.
    thinking_modes = frozenset(ThinkingMode)
    expresses_thinking_levels = True
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

    def _targets_for(
        self, model: str, addressing: dict[str, Any] | None
    ) -> tuple[tuple[str, str], ...]:
        return _targets(self._models, self.serves_publisher, model, addressing or {})

    def _url(self, model: str, method: str, addressing: dict[str, Any] | None = None) -> str:
        """The **first** target's URL, for the callers that address one place by construction.

        `models()` and the reachability probe below name a single region on purpose. Everything on
        the request path goes through `_across_regions` instead, because there the second region is
        the feature.
        """
        region, publisher = self._targets_for(model, addressing)[0]
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
        body = canonical_to_gemini_request(request)

        async def attempt(region: str, publisher: str) -> CanonicalResponse:
            url = self._transport.url(
                region=region, publisher=publisher, model=request.model, method="generateContent"
            )
            data = await self._transport.post(url, body)
            answer = gemini_response_to_canonical(data, request.model)
            # **The region that answered**, not the one the catalogue lists first. The audit row
            # takes it from here, because a residency claim naming a place the request did not go
            # to is worse than none (`FRD-115` FR-10).
            return answer.model_copy(update={"served_region": region})

        return await _across_regions(self._targets_for(request.model, request.addressing), attempt)

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        """**Failover ends at the first chunk**, and that boundary is the whole design here.

        A stream that has already sent bytes to the caller cannot be restarted somewhere else: the
        client has half an answer, and a second region would continue it with a different model's
        first sentence. So the chain is walked while *opening* — connect, and read up to the first
        chunk — and once one has been yielded, every later failure propagates untouched.

        The same rule the project already states about model fallback, one axis along: conditions
        are checked before dispatch, and a stream on the wire is committed.
        """
        body = canonical_to_gemini_request(request)
        targets = self._targets_for(request.model, request.addressing)
        opened: AsyncIterator[CanonicalChunk] | None = None

        async def attempt(region: str, publisher: str) -> AsyncIterator[CanonicalChunk]:
            url = self._transport.url(
                region=region,
                publisher=publisher,
                model=request.model,
                method="streamGenerateContent",
            )
            return await _open_stream(self._transport, f"{url}?alt=sse", body)

        opened = await _across_regions(targets, attempt)
        async for chunk in opened:
            yield chunk

    async def embed(self, request: CanonicalEmbeddingRequest) -> list[list[float]]:
        """A list goes to `batchEmbedContents`, a single text to `embedContent`.

        Not premature: the single-item endpoint has materially lower latency, and the overwhelming
        majority of embedding traffic is one text at a time.
        """
        method = "batchEmbedContents" if request.size > 1 else "embedContent"
        body = (
            batch_embedding_body(request, request.model)
            if request.size > 1
            else canonical_to_gemini_embedding(request)
        )

        async def attempt(region: str, publisher: str) -> list[list[float]]:
            url = self._transport.url(
                region=region, publisher=publisher, model=request.model, method=method
            )
            return embedding_values(await self._transport.post(url, body))

        # Embeddings take the chain too. A batch that cannot be served in one region for want of
        # quota is exactly the case failover is for, and an embedding request is often the larger
        # bill of the two.
        return await _across_regions(self._targets_for(request.model, request.addressing), attempt)

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
    #: Anthropic asks for thinking by naming `budget_tokens` and has no level field,
    #: so a level word has nowhere to go and is refused rather than dropped.
    expresses_thinking_levels = False
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

    def _targets_for(
        self, model: str, addressing: dict[str, Any] | None
    ) -> tuple[tuple[str, str], ...]:
        return _targets(self._models, self.serves_publisher, model, addressing or {})

    def _url(self, model: str, method: str, addressing: dict[str, Any] | None = None) -> str:
        """The **first** target's URL, for the callers that address one place by construction.

        `models()` and the reachability probe below name a single region on purpose. Everything on
        the request path goes through `_across_regions` instead, because there the second region is
        the feature.
        """
        region, publisher = self._targets_for(model, addressing)[0]
        return self._transport.url(region=region, publisher=publisher, model=model, method=method)

    def _body(self, request: CanonicalRequest) -> dict[str, object]:
        return canonical_to_anthropic(
            request, max_tokens=request.max_output_tokens or self._default_max_tokens
        )

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        body = self._body(request)

        async def attempt(region: str, publisher: str) -> CanonicalResponse:
            url = self._transport.url(
                region=region, publisher=publisher, model=request.model, method="rawPredict"
            )
            data = await self._transport.post(url, body)
            # The mapper has to be told a schema was asked for: with this vendor the document
            # arrives in a tool-call block that an ordinary answer would never contain, and reading
            # it back as text is the difference between a document and prose about one.
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
        """Same boundary as the Gemini adapter's: the chain is walked while opening, and a stream
        that has sent a byte is committed."""
        body = {**self._body(request), "stream": True}

        async def attempt(region: str, publisher: str) -> AsyncIterator[CanonicalChunk]:
            url = self._transport.url(
                region=region,
                publisher=publisher,
                model=request.model,
                method="streamRawPredict",
            )
            return await _open_assembled_stream(self._transport, url, body)

        targets = self._targets_for(request.model, request.addressing)
        opened = await _across_regions(targets, attempt)
        async for chunk in opened:
            yield chunk

    async def embed(self, request: CanonicalEmbeddingRequest) -> list[list[float]]:
        # Unreachable in the normal path: `FRD-114`'s declaration refuses an embedding request for
        # a model that does not declare the capability. This is a backstop for a misconfigured
        # catalog, not the mechanism.
        raise UpstreamError(f"Model '{request.model}' has no embedding endpoint.", 400)

    async def aclose(self) -> None:
        """Close the connection pool this adapter owns (`ProviderRegistry.aclose`)."""
        await self._transport.aclose()
