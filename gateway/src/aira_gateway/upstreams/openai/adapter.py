"""An upstream that speaks the OpenAI wire format (`FRD-123`).

    OpenAITransport            base URL, a credential if the platform has one, errors
    └── OpenAIAdapter          chat and embeddings, addressed through the platform's `Routes`

Implements the same ``Upstream`` protocol as every other adapter, so nothing above ``upstreams/``
learns that a third dialect exists; the architecture assertion in ``test_vertex.py`` checks it.
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
    """Models reached through an OpenAI-compatible endpoint: a plain server, or Foundry's.

    ``embedding_models`` is separate from ``models`` because the verb sets are disjoint here — a
    chat model has no embedding endpoint and vice versa — and the registry's method list should say
    so rather than leave it to `FRD-114`'s declaration.
    """

    sampling_controls = OPENAI_SAMPLING
    #: Speech is a separate endpoint in this dialect, not a field of a chat completion (`FRD-624`).
    speaks = False
    #: **No `limited` and no `auto`**: `reasoning_effort` is a word with no token budget, so an
    #: explicit count cannot be honoured exactly and "you decide" cannot be said (`FRD-111` §5.2).
    thinking_modes = frozenset(ThinkingMode) - {ThinkingMode.LIMITED, ThinkingMode.AUTO}
    expresses_thinking_levels = True
    #: `response_format` and `tools` are separate fields in this dialect.
    tools_with_schema = True

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
        # How this platform addresses a model (`ADR-0011`'s third axis); the plain form by default.
        self._routes: Routes = routes or StandardRoutes()
        self._provider = provider
        self._publisher = publisher
        # Declared even for a local endpoint: a self-hosted model is the strongest residency story
        # there is, and an audit row that records no region cannot tell it (`FRD-123` §5.3).
        self._region = region
        self._chat = list(models)
        self._embedding = list(embedding_models or [])

    @property
    def platform_label(self) -> str:
        """What to call this upstream on a screen: the configured name plus what it is, because an
        operator's `gpu-2` alone means nothing beside "Google AI Studio"."""
        kind = "OpenAI-compatible endpoint" if self._routes.names_models() else "Microsoft Foundry"
        return f"{self._provider} — {kind}"

    @property
    def serves_provider(self) -> str:
        """The provider name this adapter owns, so cataloguing a model is enough to serve it.

        The **configured server's** name: each machine of a fleet is audited under its own
        (`FRD-123`). Claimed only where the model name is the whole addressing — a catalogued
        Foundry model would resolve here and then 404 on a deployment nobody created.
        """
        return self._provider if self._routes.names_models() else ""

    @property
    def provenance(self) -> tuple[str, str, str]:
        """Stated once, so an empty configured list still produces a complete audit row."""
        return (self._provider, self._publisher, self._region)

    @property
    def enumerates(self) -> bool:
        """Whether this *instance* can be asked for a model list worth importing.

        An instance question: the same class serves a plain endpoint, whose listing names models a
        caller can use, and Foundry, whose listing does not.
        """
        return self._routes.names_models()

    @property
    def probe_name(self) -> str:
        """How this adapter appears in `/readyz`: the configured name, so several servers of one
        kind are distinguishable (`FRD-123`)."""
        return self._provider

    def models(self) -> list[UpstreamModel]:
        return [
            UpstreamModel(name, name, CHAT_METHODS, self._provider, self._publisher, self._region)
            for name in self._chat
        ] + [
            UpstreamModel(name, name, EMBED_METHODS, self._provider, self._publisher, self._region)
            for name in self._embedding
        ]

    async def available_models(self) -> list[OfferedModel]:
        """The endpoint's own listing, as bare names.

        It publishes no capabilities, so every one stays ``None`` — *the vendor said nothing*. A
        chat server listing a model does not declare that the model can chat.
        """
        listing = await self._transport.get(self._routes.listing())
        entries = listing.get("data") or []
        return [
            OfferedModel(name=str(entry["id"]))
            for entry in entries
            if isinstance(entry, dict) and entry.get("id")
        ]

    async def ping(self, model: str = "", addressing: dict[str, str] | None = None) -> str:
        """The cheapest remote question there is (`FRD-117` §5.2): a GET of the listing.

        Never a generation, which would cost money and wake a scaled-to-zero model on every check.
        """
        listing = await self._transport.get(self._routes.listing())
        count = len(listing.get("data") or [])
        return f"{count} model(s) listed" if count else "endpoint answered"

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        body = self._named(canonical_to_openai(request), request.model)
        data = await self._transport.post(self._routes.chat(request.model), body)
        # The use case's switch, carried to the mapper: this dialect returns reasoning whether or
        # not it was asked for (`FRD-135` FR-3).
        return openai_to_canonical(data, request.model, include_reasoning=request.include_reasoning)

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        body = self._named(canonical_to_openai(request, stream=True), request.model)
        # Tool calls arrive in fragments and are assembled here: assembling is stateful, and the
        # per-chunk mapper is not (`FRD-131` FR-6).
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

    async def embed(self, request: CanonicalEmbeddingRequest) -> list[list[float]]:
        body = self._named(canonical_to_openai_embedding(request), request.model)
        data = await self._transport.post(self._routes.embed(request.model), body)
        return embedding_values(data)

    async def aclose(self) -> None:
        """Close the connection pool this adapter owns (`ProviderRegistry.aclose`)."""
        await self._transport.aclose()

    def _named(self, body: dict[str, Any], model: str) -> dict[str, Any]:
        """Set the body's model field the way the *platform* wants it (`FRD-120` §5.1).

        The dialect always writes one and a platform that addresses by path takes it out; doing it
        here keeps the dialect platform-free.
        """
        named = self._routes.body_model(model)
        if named is None:
            body.pop("model", None)
        else:
            body["model"] = named
        return body


def _tool_call_deltas(payload: dict[str, Any]) -> Any:
    """The `delta.tool_calls` of one SSE payload, or nothing."""
    choices = payload.get("choices") or []
    if not choices:
        return ()
    return (choices[0].get("delta") or {}).get("tool_calls") or ()
