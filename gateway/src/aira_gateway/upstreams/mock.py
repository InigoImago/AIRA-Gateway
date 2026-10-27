"""Deterministic mock upstream provider for demo mode (`FRD-002`, `FRD-100`).

Canned but plausible, fully deterministic completions and embeddings, so the whole system works
end-to-end without upstream credentials.

The mock **honours every option it is given** — attachments, thinking, caching, response schema,
tools, task type, dimensionality — and says what it saw. A mock that ignored them would let every
hermetic test pass while the real path was broken.
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
    CanonicalRerankRequest,
    CanonicalResponse,
    CanonicalUsage,
    DataPart,
    FinishReason,
    RankedRecord,
    RankedRecords,
    ToolCallPart,
    ToolMode,
)
from aira_gateway.core.schema import ResponseSchema, SchemaType
from aira_gateway.upstreams.base import OfferedModel, UpstreamModel

_STREAM_WORDS_PER_CHUNK = 3

#: What the mock streams as its thought, where one was asked for.
_MOCK_THOUGHT = "[mock thought] "

#: The signature a thinking model puts on its function call, which the caller has to hand back on
#: that call — base64, as Google's own are, since its SDKs decode the field into bytes and encode
#: it again. Fixed, so a test can tell that it came back.
MOCK_SIGNATURE = "YWlyYSBtb2NrIHRob3VnaHQgc2lnbmF0dXJl"

_DEFAULT_DIMENSIONS = 8

#: What the mock's speech sounds like, in the numbers Google's speech models use (`FRD-624`):
#: 24 kHz, 16-bit, one channel, and about 25 output tokens per second of audio.
SPEECH_MEDIA_TYPE = "audio/L16;codec=pcm;rate=24000"
_SPEECH_RATE = 24000
_SPEECH_SECONDS_PER_WORD = 0.25
_SPEECH_TOKENS_PER_SECOND = 25
_SPEECH_CHUNK_BYTES = 4800
#: The longest the mock speaks, however long the prompt: its audio grows with the text, and an
#: unbounded prompt would be an unbounded buffer of fiction built in memory, on the event loop.
_SPEECH_MAX_SECONDS = 60.0

#: How much of the prompt a string the mock makes up repeats, in a structured answer and a tool's
#: arguments alike. A schema's answer holds up to 256 strings, so the prompt copied whole into each
#: is an answer 256 times the prompt, built in memory on the event loop — and the mock serves
#: every demo deployment, which the published demo key reaches.
ECHOED = 40


class MockProvider:
    """A deterministic, offline provider; ``mock-1`` unless a test names its models."""

    #: A **test double**, not a model: `FRD-307`'s approval governs model access, and there is no
    #: model here. `create_app` registers it only in the `local` environment.
    is_test_double = True
    #: The provider name this adapter owns (`FRD-507`), so the picker, listing and import can be
    #: demonstrated on a laptop. **No region**: a region is a claim `RegionAllowed` checks, and
    #: nothing runs anywhere.
    platform_label = "Mock — deterministic fiction, local only"
    serves_provider = "mock"
    provenance = (serves_provider, "aira", "")
    enumerates = True
    #: Declared rather than inherited, like every adapter's: our own code honours all of them.
    sampling_controls = frozenset(SAMPLING_CONTROLS)
    thinking_modes = frozenset(ThinkingMode)
    expresses_thinking_levels = True
    #: Speaks deterministic PCM, so the speech path is tested without a cloud (`FRD-624`).
    speaks = True

    def __init__(self, *models: str) -> None:
        """One adapter, however many models a test needs — two adapters claiming ``mock`` would be
        the ambiguity `ProviderRegistry` refuses to boot on."""
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

    def models(self) -> list[UpstreamModel]:
        return list(self._models)

    async def available_models(self) -> list[OfferedModel]:
        """What this "vendor" offers: the two verbs it implements, and **silence on the rest**.

        Claiming every capability would make the import's subject — a vendor's silence must not
        become a declaration — true by construction and untested.
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
        if request.speech is not None:
            return self._speech(request)
        choice = request.tool_choice.mode if request.tool_choice else ToolMode.AUTO
        # `none` forbids a call, and is answered in prose like a request with no functions.
        if request.tools and choice is not ToolMode.NONE:
            return self._tool_call(request)
        if request.structured:
            return self._structured(request)

        prompt = request.last_user_text().strip().replace("\n", " ")[:120]
        words = (
            f"[mock:{request.model}] response to: {prompt}"
            f"{_attachments(request)}{_thinking(request)}{_caching(request)}"
        ).split()

        finish_reason = FinishReason.STOP
        limit = request.max_output_tokens
        if limit is not None and limit < len(words):
            words = words[:limit]
            finish_reason = FinishReason.MAX_TOKENS

        usage = CanonicalUsage(
            prompt_tokens=self._prompt_tokens(request),
            # Thinking is billed as output by every provider that has it, so budget tests see it.
            completion_tokens=len(words) + _thinking_tokens(request),
        )
        return CanonicalResponse(
            model=request.model, text=" ".join(words), finish_reason=finish_reason, usage=usage
        )

    def _speech(self, request: CanonicalRequest) -> CanonicalResponse:
        """Deterministic PCM whose length follows the text (`FRD-624`), up to a minute.

        Derived from the voice and the text, so two prompts sound different and one prompt always
        the same. The output cap truncates it and still says `stop`, as Google does. Built by
        repeating a 32-byte seed, not byte by byte: this runs on the event loop.
        """
        assert request.speech is not None
        text = request.last_user_text().strip()
        voices = request.speech.voice or ",".join(entry.voice for entry in request.speech.speakers)
        seconds = min(max(1, len(text.split())) * _SPEECH_SECONDS_PER_WORD, _SPEECH_MAX_SECONDS)
        tokens = max(1, round(seconds * _SPEECH_TOKENS_PER_SECOND))
        size = int(_SPEECH_RATE * seconds) * 2
        limit = request.max_output_tokens
        if limit is not None and limit < tokens:
            size = size * limit // tokens // 2 * 2
            tokens = limit
        seed = hashlib.sha256(f"{voices}\x00{text}".encode()).digest()
        data = (seed * (size // len(seed) + 1))[:size]
        usage = CanonicalUsage(prompt_tokens=self._prompt_tokens(request), completion_tokens=tokens)
        return CanonicalResponse(
            model=request.model,
            text="",
            audio=DataPart(media_type=SPEECH_MEDIA_TYPE, data=data),
            usage=usage,
        )

    def _tool_call(self, request: CanonicalRequest) -> CanonicalResponse:
        """Answer a request that declares tools by **calling the first one** (`FRD-131`).

        Deterministic: each declared property gets a value of its declared type, a string being
        the caller's prompt. Typed, because a real client validates the arguments against the
        schema it declared, and a `"true"` for a boolean fails there before the loop it tests runs.
        A request that already carries a tool result gets prose instead, or a tool loop would never
        end — unless a call is required (`any`), which also names the function where it narrows
        them, as a model held to `allowedFunctionNames` would.
        """
        choice = request.tool_choice
        required = choice is not None and choice.mode is ToolMode.ANY
        if not required and any(message.tool_results for message in request.messages):
            usage = CanonicalUsage(prompt_tokens=self._prompt_tokens(request), completion_tokens=6)
            return CanonicalResponse(
                model=request.model,
                text=f"[mock:{request.model}] acted on the tool result",
                usage=usage,
            )

        allowed = choice.allowed if choice is not None else ()
        tool = next((t for t in request.tools if t.name in allowed), request.tools[0])
        properties: Any = (
            (tool.parameters.properties or {})
            if tool.parameters is not None
            else (tool.parameters_json_schema.document.get("properties") or {})
            if tool.parameters_json_schema is not None
            else {}
        )
        prompt = request.last_user_text().strip()[:60]
        arguments = {name: _argument(child, prompt) for name, child in properties.items()}
        usage = CanonicalUsage(prompt_tokens=self._prompt_tokens(request), completion_tokens=8)
        return CanonicalResponse(
            model=request.model,
            text="",
            finish_reason=FinishReason.TOOL_USE,
            usage=usage,
            tool_calls=(
                ToolCallPart(
                    id=f"mock-{tool.name}",
                    name=tool.name,
                    arguments=arguments,
                    # A thinking model signs its call, and validates the signature on the turn
                    # that continues it.
                    signature=MOCK_SIGNATURE if _thinking_on(request) else None,
                ),
            ),
        )

    def _structured(self, request: CanonicalRequest) -> CanonicalResponse:
        """A document conforming to the submitted schema (`FRD-112` §12).

        Truncated at the output cap the way a real provider stops mid-document, because that is the
        failure `FRD-112` FR-6 refuses, and a mock that always finished would never exercise it.
        """
        document = (
            synthesise(request.response_schema, request.last_user_text())
            if request.response_schema is not None
            else synthesise_json(
                request.response_json_schema.document if request.response_json_schema else {},
                request.last_user_text(),
            )
        )
        text = json.dumps(document, separators=(",", ":"))
        limit = request.max_output_tokens
        truncated = limit is not None and limit < max(1, len(text) // 4)
        return CanonicalResponse(
            model=request.model,
            text=text[: limit * 4] if truncated and limit else text,
            finish_reason=FinishReason.MAX_TOKENS if truncated else FinishReason.STOP,
            usage=CanonicalUsage(
                prompt_tokens=self._prompt_tokens(request),
                completion_tokens=max(1, len(text) // 4) + _thinking_tokens(request),
            ),
        )

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        full = await self.generate(request)
        # Speech arrives in pieces, as Google streams it, and the pieces join to the whole answer.
        audio = full.audio.data if full.audio is not None else b""
        for start in range(0, len(audio), _SPEECH_CHUNK_BYTES):
            yield CanonicalChunk(
                text_delta="",
                audio_delta=DataPart(
                    media_type=SPEECH_MEDIA_TYPE, data=audio[start : start + _SPEECH_CHUNK_BYTES]
                ),
            )
        if _thinks_aloud(request):
            # A thought ahead of the answer, as a thinking model streams one where it was asked
            # for — so a client's handling of a streamed thought is testable without a cloud.
            yield CanonicalChunk(text_delta="", reasoning_delta=_MOCK_THOUGHT)
        words = full.text.split()
        for start in range(0, len(words), _STREAM_WORDS_PER_CHUNK):
            delta = " ".join(words[start : start + _STREAM_WORDS_PER_CHUNK])
            yield CanonicalChunk(text_delta=f"{delta} ")
        # Tool calls ride the final chunk, whole, as a real dialect delivers them (`FRD-131` FR-6).
        yield CanonicalChunk(
            text_delta="",
            finish_reason=full.finish_reason,
            usage=full.usage,
            tool_calls=full.tool_calls,
        )

    async def embed(self, request: CanonicalEmbeddingRequest) -> list[list[float]]:
        """One vector per text, of the requested width.

        The values depend on the text **and the task type**, so a test can prove two task types
        produce different vectors without a cloud call.
        """
        dimensions = request.dimensions or _DEFAULT_DIMENSIONS
        # A task written into the text is part of the text, as it is to the real model.
        task = None if request.task_in_text else request.task_type
        if request.aggregate:
            # One vector for every part together, as Google combines the parts of one content.
            return [self._vector("\x1f".join(request.texts), task, dimensions)]
        return [self._vector(text, task, dimensions) for text in request.texts]

    async def rerank(self, request: CanonicalRerankRequest) -> RankedRecords:
        """Each record scored by how many of the query's words it contains, best first.

        Deterministic and visibly an ordering, so the demo shows one and a test can name it. Ties
        keep the order sent.
        """
        words = set(request.query.lower().split())
        scored = sorted(
            (
                RankedRecord(
                    id=record.id,
                    score=_overlap(words, f"{record.title} {record.content}"),
                    title=None if request.ids_only else record.title or None,
                    content=None if request.ids_only else record.content or None,
                )
                for record in request.records
            ),
            key=lambda record: -record.score,
        )
        return RankedRecords(scored[: request.top_n] if request.top_n else scored)

    @staticmethod
    def _vector(text: str, task_type: str | None, dimensions: int) -> list[float]:
        seed = f"{task_type or ''}\x00{text}".encode()
        data = hashlib.sha256(seed).digest()
        return [data[i % len(data)] / 255.0 for i in range(dimensions)]

    @staticmethod
    def _prompt_tokens(request: CanonicalRequest) -> int:
        # Attachments cost input tokens no word count predicts: a coarse 250 per KiB, so a budget
        # test against a document never measures a request that looked free.
        attachment_tokens = sum(part.size // 4 for part in request.attachments)
        words = sum(len(message.text.split()) for message in request.messages)
        return words + attachment_tokens


def _overlap(words: set[str], text: str) -> float:
    """The share of ``words`` that ``text`` contains, from 0 to 1."""
    if not words:
        return 0.0
    found = set(text.lower().split())
    return round(len(words & found) / len(words), 4)


def _thinking_on(request: CanonicalRequest) -> bool:
    """Whether the model is thinking on this request."""
    return request.thinking is not None and request.thinking.mode != ThinkingMode.DISABLED


def _thinks_aloud(request: CanonicalRequest) -> bool:
    """Whether a thought is streamed: the use case wants reasoning, and the model is thinking."""
    return request.include_reasoning and _thinking_on(request)


def _thinking(request: CanonicalRequest) -> str:
    """Say what thinking was asked for, so the resolution is observable without a cloud."""
    setting = request.thinking
    if setting is None:
        return ""
    budget = f" budget={setting.tokens}" if setting.tokens is not None else ""
    return f" [thinking:{setting.mode}{budget}]"


def _caching(request: CanonicalRequest) -> str:
    """Say whether the stable prefix was marked cacheable, and for how long (`FRD-133`).

    The marker and nothing else: reporting cached tokens would make every "caching saves money"
    assertion true by construction. The setting crosses the console, an event and a read-model
    before it gets here, and a request served uncached looks exactly like one never asked to cache.
    """
    return f" [cache:{request.cache_ttl}]" if request.cache_prefix else ""


def _thinking_tokens(request: CanonicalRequest) -> int:
    setting = request.thinking
    if setting is None or setting.mode == ThinkingMode.DISABLED:
        return 0
    # Half the budget: a large budget is visibly dearer than none, without pretending to know more.
    return (setting.tokens or 0) // 2


def _attachments(request: CanonicalRequest) -> str:
    """Describe what was attached, deterministically."""
    parts = request.attachments
    if not parts:
        return ""
    described = ", ".join(f"{part.media_type} ({part.size} bytes)" for part in parts)
    return f" [with {len(parts)} attachment(s): {described}]"


def synthesise(schema: ResponseSchema, prompt: str = "") -> Any:
    """A minimal value conforming to ``schema``, derived from the prompt so it is deterministic.

    Honours ``type``, ``properties``, ``required``, ``items``, ``enum`` and ``anyOf`` — what a test
    needs to assert the document has the requested shape — and is not a general generator.
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
            # One element, not zero: an empty array would let a shape assertion pass vacuously.
            return [synthesise(schema.items, prompt)] if schema.items is not None else []
        case SchemaType.INTEGER:
            return len(prompt)
        case SchemaType.NUMBER:
            return float(len(prompt))
        case SchemaType.BOOLEAN:
            return bool(len(prompt) % 2)
        case SchemaType.NULL:
            return None
        case _:
            return schema.title or schema.description or f"mock:{prompt[:ECHOED]}"


def _argument(schema: Any, prompt: str) -> Any:
    """One tool argument of the declared type: the prompt for a string, a minimal value otherwise,
    from either way a tool may declare it — Google's subset or JSON Schema."""
    if isinstance(schema, ResponseSchema):
        return prompt if schema.type in (None, SchemaType.STRING) else synthesise(schema, prompt)
    return synthesise_json(schema, prompt)


def synthesise_json(schema: Any, text: str) -> Any:
    """A document matching a JSON Schema's shape, for the mock's structured answers (`FRD-143`):
    every declared property, the first of a choice, the prompt's first `ECHOED` characters for
    every string."""
    text = text[:ECHOED]
    if not isinstance(schema, dict):
        return text
    if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
        return schema["enum"][0]
    for choice in ("anyOf", "oneOf"):
        if isinstance(schema.get(choice), list) and schema[choice]:
            return synthesise_json(schema[choice][0], text)
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((entry for entry in kind if entry != "null"), "null")
    if kind == "object" or "properties" in schema:
        properties = schema.get("properties")
        return {
            name: synthesise_json(value, text)
            for name, value in (properties.items() if isinstance(properties, dict) else ())
        }
    if kind == "array":
        return [synthesise_json(schema.get("items", {}), text)]
    return {"integer": 1, "number": 1.0, "boolean": True, "null": None}.get(str(kind), text)
