"""Deterministic mock upstream provider for demo mode (FRD-002 / FRD-100).

Produces canned but plausible, fully deterministic completions and embeddings so the whole system
works end-to-end without real upstream credentials.

The mock **honours every option it is given** — attachments, thinking, response schema, task type,
dimensionality — because a mock that ignored them would let every hermetic test pass while the real
path was broken, and the features would then only ever be exercised against a cloud nobody has in
CI. So it sees what it was sent, and says what it saw.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any

from aira_common.models import ThinkingMode
from aira_gateway.core.canonical import (
    SAMPLING_CONTROLS,
    CanonicalChunk,
    CanonicalEmbeddingRequest,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    ToolCallPart,
)
from aira_gateway.core.schema import ResponseSchema, SchemaType
from aira_gateway.upstreams.base import OfferedModel, UpstreamModel

_STREAM_WORDS_PER_CHUNK = 3
_DEFAULT_DIMENSIONS = 8


class MockProvider:
    #: This is a **test double**, not a model.
    #:
    #: `FRD-307` requires every usable model to be catalogued and approved by a Global
    #: Administrator. That question is about governing *model access*, and there is no model here:
    #: the answers are deterministic fiction, they cost nothing, and approving them would be
    #: theatre. The exemption is bounded by where this provider is registered at all — see
    #: `create_app`, which now leaves it out of any environment but `local`.
    is_test_double = True

    """A deterministic, offline provider exposing a single ``mock-1`` model."""

    #: The provider name this adapter owns (`FRD-507`), and where it says its answers come from.
    #:
    #: Stated for the same reason every option is honoured above: a feature that can only be
    #: exercised against a cloud nobody has in CI is a feature nobody exercises. The provider
    #: picker, the vendor listing and the import are all demonstrable on a laptop because this
    #: names itself — and `local` is the only environment it is registered in, so nothing in a
    #: deployment can be catalogued under it.
    #: **No region, deliberately.** The same rule `build_openai_upstreams` records: a region is a
    #: claim, and `RegionAllowed` checks every model that makes one — so declaring `local` here
    #: made every mock request fail residency under the EU default, which is a correct control
    #: refusing a fiction. Nothing runs anywhere; there is nothing to claim.
    platform_label = "Mock — deterministic fiction, local only"
    serves_provider = "mock"
    provenance = (serves_provider, "aira", "")
    enumerates = True

    def __init__(self, *models: str) -> None:
        """One adapter, however many models a test needs.

        It took one model until 2026-08-10, so a test wanting two registered **two adapters** — and
        the moment this provider owned a name, that became two adapters claiming it, which is the
        ambiguity `ProviderRegistry` refuses to boot on and refuses correctly. The registry was
        right and the expression was wrong: those tests never meant two upstreams, they meant one
        upstream serving two models.
        """
        self._models = [
            UpstreamModel(
                name=name,
                version=name,
                supported_methods=("generateContent", "streamGenerateContent", "embedContent"),
                provider=self.serves_provider,
                publisher="aira",
            )
            for name in (models or ("mock-1",))
        ]

    #: The mock is our own code, so it can honour anything the canonical request carries — and
    #: it must declare that rather than inherit it, or the declaration would be optional in
    #: practice and the one adapter that forgot would be the one that mattered.
    sampling_controls = frozenset(SAMPLING_CONTROLS)
    #: Everything, deterministically — the mock exists so a mode is observable without a cloud.
    thinking_modes = frozenset(ThinkingMode)
    #: Everything, so a level word is observable without a cloud.
    expresses_thinking_levels = True

    #: The mock keeps a schema and the caller's tools apart, as Gemini and the OpenAI dialect do.
    #: Declared rather than inherited, for the reason the sampling set above is (`FRD-131` FR-5).
    tools_with_schema = True

    def models(self) -> list[UpstreamModel]:
        return list(self._models)

    async def available_models(self) -> list[OfferedModel]:
        """What this "vendor" offers — which for a deterministic double is what it serves.

        It states the two verbs it really implements and **says nothing about the rest**, exactly
        as a real listing does: a mock that claimed every capability would make the import's whole
        subject — that a vendor's silence must not become a declaration — true by construction and
        untested (`FRD-133` made the same choice about never reporting a cache hit).
        """
        return [
            OfferedModel(
                name=model.name,
                display_name="Mock model (deterministic fiction)",
                description="Answers without contacting anything. Not a model.",
                can_generate=True,
                can_embed=True,
                can_cache_prompts=False,
            )
            for model in self._models
        ]

    async def ping(self, model: str = "", addressing: dict[str, str] | None = None) -> str:
        """Answers instantly and contacts nothing, which is the honest verdict for a double."""
        return f"{len(self._models)} model(s) listed"

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        if request.tools:
            return self._tool_call(request)
        if request.response_schema is not None:
            return self._structured(request)

        prompt = request.last_user_text().strip().replace("\n", " ")[:120]
        words = (
            f"[mock:{request.model}] response to: {prompt}"
            f"{_attachments(request)}{_thinking(request)}{_caching(request)}"
        ).split()

        finish_reason = "stop"
        limit = request.max_output_tokens
        if limit is not None and limit < len(words):
            words = words[:limit]
            finish_reason = "max_tokens"

        usage = CanonicalUsage(
            prompt_tokens=self._prompt_tokens(request),
            # Thinking tokens are billed as output tokens by every provider that has the feature,
            # and the mock reports them that way — otherwise the budget tests would measure a
            # 20 000-token reservation settling against a figure that never saw it.
            completion_tokens=len(words) + _thinking_tokens(request),
        )
        return CanonicalResponse(
            model=request.model, text=" ".join(words), finish_reason=finish_reason, usage=usage
        )

    def _tool_call(self, request: CanonicalRequest) -> CanonicalResponse:
        """Answer a request that declares tools by **asking for the first one** (`FRD-131`).

        The mock honours what it is given, and this is the option where that matters most: without
        it, tool calling would only ever be exercised against a model nobody has in CI — which is
        exactly the state `FRD-110` refused to leave attachments in.

        Deterministic, so a test can assert on it: the first declared function, with each of its
        required properties filled from the caller's own prompt. A model choosing *not* to call is
        also a real answer, and a request whose last turn already carries a tool result gets prose
        instead — otherwise the mock would loop forever and no test could end.
        """
        if any(message.tool_results for message in request.messages):
            usage = CanonicalUsage(prompt_tokens=self._prompt_tokens(request), completion_tokens=6)
            return CanonicalResponse(
                model=request.model,
                text=f"[mock:{request.model}] acted on the tool result",
                usage=usage,
            )

        tool = request.tools[0]
        properties = (tool.parameters.properties or {}) if tool.parameters is not None else {}
        prompt = request.last_user_text().strip()[:60]
        arguments = dict.fromkeys(properties, prompt)
        usage = CanonicalUsage(prompt_tokens=self._prompt_tokens(request), completion_tokens=8)
        return CanonicalResponse(
            model=request.model,
            text="",
            finish_reason="tool_use",
            usage=usage,
            tool_calls=(ToolCallPart(id=f"mock-{tool.name}", name=tool.name, arguments=arguments),),
        )

    def _structured(self, request: CanonicalRequest) -> CanonicalResponse:
        """A document conforming to the submitted schema (`FRD-112` §12).

        Enough to demonstrate the whole path — including the routing interaction — with no cloud
        credentials, and enough for a test to assert that what comes back actually parses.
        """
        assert request.response_schema is not None
        document = synthesise(request.response_schema, request.last_user_text())
        text = json.dumps(document, separators=(",", ":"))
        # A real provider stops at the output cap mid-document, and the result is valid-looking
        # JSON right up to where it stops. The mock models that, because it is the exact failure
        # `FRD-112` FR-6 exists to refuse — and a mock that always finished cleanly would leave
        # that check exercised by nothing at all.
        limit = request.max_output_tokens
        truncated = limit is not None and limit < max(1, len(text) // 4)
        return CanonicalResponse(
            model=request.model,
            text=text[: limit * 4] if truncated and limit else text,
            finish_reason="max_tokens" if truncated else "stop",
            usage=CanonicalUsage(
                prompt_tokens=self._prompt_tokens(request),
                completion_tokens=max(1, len(text) // 4) + _thinking_tokens(request),
            ),
        )

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        full = await self.generate(request)
        words = full.text.split()
        for start in range(0, len(words), _STREAM_WORDS_PER_CHUNK):
            delta = " ".join(words[start : start + _STREAM_WORDS_PER_CHUNK])
            yield CanonicalChunk(text_delta=f"{delta} ")
        # Tool calls ride the **final** chunk, whole, exactly as a real dialect delivers them
        # (`FRD-131` FR-6). A mock that streamed text and dropped the call would leave the entire
        # streamed path untested — which is how the audit gap this comment sits next to survived
        # stages 1-4 and was found against a running model instead.
        yield CanonicalChunk(
            text_delta="",
            finish_reason=full.finish_reason,
            usage=full.usage,
            tool_calls=full.tool_calls,
        )

    async def embed(self, request: CanonicalEmbeddingRequest) -> list[list[float]]:
        """One vector per text, of the requested width.

        The values depend on the text **and the task type**, so a test can prove two task types
        produce different vectors without a cloud call — which is the property that makes the task
        type worth validating rather than passing through.
        """
        dimensions = request.dimensions or _DEFAULT_DIMENSIONS
        return [self._vector(text, request.task_type, dimensions) for text in request.texts]

    @staticmethod
    def _vector(text: str, task_type: str | None, dimensions: int) -> list[float]:
        seed = f"{task_type or ''}\x00{text}".encode()
        data = hashlib.sha256(seed).digest()
        return [data[i % len(data)] / 255.0 for i in range(dimensions)]

    @staticmethod
    def _prompt_tokens(request: CanonicalRequest) -> int:
        # Attachments cost input tokens that no character count predicts, and the mock has to say
        # so or every budget test against a document would measure a request that looked free.
        # 250 per 1 KiB is a coarse stand-in for what a provider actually charges — the point is
        # that it is not zero.
        attachment_tokens = sum(part.size // 4 for part in request.attachments)
        words = sum(len(message.text.split()) for message in request.messages)
        return words + attachment_tokens


def _thinking(request: CanonicalRequest) -> str:
    """Say what thinking was asked for, so the resolution is observable without a cloud."""
    setting = request.thinking
    if setting is None:
        return ""
    budget = f" budget={setting.tokens}" if setting.tokens is not None else ""
    return f" [thinking:{setting.mode}{budget}]"


def _caching(request: CanonicalRequest) -> str:
    """Say whether the stable prefix was marked cacheable, and for how long (`FRD-133`).

    The marker and nothing else. A mock that also reported cached tokens would make every
    "caching saves money" assertion true by construction, and whether a prefix is actually hit is
    the one thing about this feature that only a real provider can tell us — the same reason
    `FRD-123` went looking for a model that had never agreed to anything.

    Worth saying at all because the setting travels a long way to get here: a checkbox in the
    console, an event, a read-model row, a lookup after routing. Every hop is somewhere it can be
    dropped without anything failing, and a request served uncached looks exactly like one that
    was never asked to cache.
    """
    return f" [cache:{request.cache_ttl}]" if request.cache_prefix else ""


def _thinking_tokens(request: CanonicalRequest) -> int:
    setting = request.thinking
    if setting is None or setting.mode == ThinkingMode.DISABLED:
        return 0
    # Half the budget: enough that a large budget is visibly more expensive than none, without
    # pretending the mock knows how much a model would really have spent.
    return (setting.tokens or 0) // 2


def _attachments(request: CanonicalRequest) -> str:
    """Describe what was attached, deterministically.

    A mock that ignored attachments would let every hermetic test pass while the real path was
    broken — and the whole document feature would be exercised only against a cloud nobody has in
    CI. So the mock *sees* them, and says what it saw.
    """
    parts = request.attachments
    if not parts:
        return ""
    described = ", ".join(f"{part.media_type} ({part.size} bytes)" for part in parts)
    return f" [with {len(parts)} attachment(s): {described}]"


def synthesise(schema: ResponseSchema, prompt: str = "") -> Any:
    """A minimal value conforming to ``schema``, derived from the prompt so it is deterministic.

    Not a general JSON-Schema generator and not trying to be: it honours ``type``, ``properties``,
    ``required``, ``items``, ``enum`` and ``anyOf``, which is what a test needs to assert that the
    document has the shape that was asked for.
    """
    if schema.any_of:
        return synthesise(schema.any_of[0], prompt)
    if schema.enum:
        return schema.enum[0]

    match schema.type:
        case SchemaType.OBJECT:
            keys = schema.property_ordering or list((schema.properties or {}).keys())
            return {
                key: synthesise(child, prompt)
                for key in keys
                if (child := (schema.properties or {}).get(key)) is not None
            }
        case SchemaType.ARRAY:
            # One element, not zero: an empty array satisfies most schemas and demonstrates
            # nothing, so a test asserting the element's shape would pass vacuously.
            return [synthesise(schema.items, prompt)] if schema.items is not None else []
        case SchemaType.INTEGER:
            return len(prompt)
        case SchemaType.NUMBER:
            return float(len(prompt))
        case SchemaType.BOOLEAN:
            return bool(len(prompt) % 2)
        case _:
            return schema.title or schema.description or f"mock:{prompt[:40]}"
