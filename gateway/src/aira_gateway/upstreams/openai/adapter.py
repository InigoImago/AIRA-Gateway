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
from aira_gateway.upstreams.base import OfferedModel, UpstreamModel
from aira_gateway.upstreams.openai.mapping import (
    SAMPLING as OPENAI_SAMPLING,
)
from aira_gateway.upstreams.openai.mapping import (
    StreamedToolCalls,
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

    @property
    def platform_label(self) -> str:
        """What to call this upstream on a screen.

        The configured name plus what it is, because the name alone is whatever an operator typed
        — `local`, `gpu-2`, `ollama` — and a picker offering those beside "Google AI Studio" is
        asking somebody to remember which is which. The label is derived rather than configured:
        one more thing to fill in per server is one more thing to leave blank.
        """
        kind = "OpenAI-compatible endpoint" if self._routes.names_models() else "Microsoft Foundry"
        return f"{self._provider} — {kind}"

    @property
    def serves_provider(self) -> str:
        """The provider name this adapter owns, so cataloguing a model is enough to serve it.

        Stage B gave the Generative Language adapter this and stopped there, which left the import
        flow offering a local model that could be catalogued and then would not answer: the
        configured list was still the only way in, so an imported entry was a declaration with
        nothing behind it — `FRD-206`'s "an action nobody can carry out", reached by a longer road.

        The name is the **configured server's**, not the class's: a self-hosted fleet is several
        machines, each audited under its own name (`FRD-123`), and two adapters claiming one name
        already refuse to boot.

        **Claimed only where the model name is the whole addressing**, which is the same predicate
        that decides whether the listing is worth asking for — so Foundry, which builds this very
        class, claims nothing: a catalogued Azure model would resolve here and then fail on a
        deployment nobody created, and the failure would arrive as a 404 that reads as "the model
        is gone". Vertex declares no name either, for the other reason: two adapters serve that
        platform and a name that identifies neither cannot route.
        """
        return self._provider if self._routes.names_models() else ""

    @property
    def provenance(self) -> tuple[str, str, str]:
        """Stated once, so an empty configured list still produces a complete audit row.

        The same correction stage B had to make for Google: provenance is read from the registry,
        and a catalogue-resolved model has no entry there. An empty residency column is worse than
        the second list this removes — "the configuration says on-premises" is a claim and "this
        request went to on-premises" is evidence, and blank is neither.
        """
        return (self._provider, self._publisher, self._region)

    @property
    def enumerates(self) -> bool:
        """Whether this *instance* can be asked for a model list worth importing.

        An instance question, not a class one, which is why `Enumerable` carries the flag rather
        than letting an ``isinstance`` decide: the same class serves a plain endpoint and Azure,
        and only one of them lists names a caller can use.
        """
        return self._routes.names_models()

    async def available_models(self) -> list[OfferedModel]:
        """The endpoint's own listing, as names and nothing else.

        Bare on purpose. This dialect's listing publishes an id, an owner and a timestamp — no
        context window, no method list, no capabilities — so every capability stays ``None``:
        *the vendor said nothing*. It would be one line to fill in `can_generate=True` on the
        grounds that a chat server serves chat models, and that line would turn an assumption into
        a declaration on a screen whose whole subject is that a declaration is a measurement.
        """
        listing = await self._transport.get(self._routes.listing())
        entries = listing.get("data") or []
        return [
            OfferedModel(name=str(entry["id"]))
            for entry in entries
            if isinstance(entry, dict) and entry.get("id")
        ]

    sampling_controls = OPENAI_SAMPLING
    #: `response_format` and `tools` are separate fields in this dialect.
    tools_with_schema = True

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
        # Tool calls arrive fragmented across deltas and are assembled here, because assembling
        # them is *stateful* and the per-chunk mapper is deliberately not (`FRD-131` FR-6).
        calls = StreamedToolCalls()
        async with self._transport.stream(self._routes.chat(request.model), body) as response:
            async for line in response.aiter_lines():
                payload = parse_sse_line(line)
                if payload is None:
                    continue
                calls.add(_tool_call_deltas(payload))
                chunk = openai_chunk_to_canonical(payload)
                if chunk is None:
                    continue
                if chunk.finish_reason is not None and calls.pending:
                    # Emitted whole, on the chunk that ends the message — never in pieces.
                    chunk = chunk.model_copy(update={"tool_calls": calls.finish()})
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

    async def aclose(self) -> None:
        """Close the connection pool this adapter owns (`ProviderRegistry.aclose`)."""
        await self._transport.aclose()


def _tool_call_deltas(payload: dict[str, Any]) -> Any:
    """The `delta.tool_calls` of one SSE payload, or nothing.

    Its own function so the stream loop reads as what it is — accumulate, then map — rather than
    burying two levels of optional indexing in a condition.
    """
    choices = payload.get("choices") or []
    if not choices:
        return ()
    return (choices[0].get("delta") or {}).get("tool_calls") or ()
