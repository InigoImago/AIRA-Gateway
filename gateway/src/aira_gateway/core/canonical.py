"""Provider-agnostic canonical request/response schema (`FRD-100`, `FRD-110`).

Every API surface maps to and from these models and every upstream speaks only canonical: this is
the single point the whole gateway agrees on (`ADR-0010`).

A message is an **ordered list of parts** — text, inline data, tool calls and tool results — and
order is kept end to end, because "this image, then this question" is a different prompt from the
reverse. ``.text`` reads what a message *says* and is **lossy**: anything that decides or persists
on it must be reviewed for what it cannot see (`FRD-110` FR-9).
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from aira_gateway.core.schema import ResponseSchema

#: The sampling controls a request can carry beyond `temperature` (`FRD-124`). Listed because **no
#: dialect expresses all of them** — OpenAI has no `top_k`, Anthropic no `seed` and no penalties —
#: so whether a candidate can honour a request must be answerable, and "it cannot" is a refusal
#: rather than a dropped field (`ADR-0011` rule 3).
SAMPLING_CONTROLS = (
    "top_p",
    "top_k",
    "seed",
    "presence_penalty",
    "frequency_penalty",
    "stop_sequences",
)


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    MODEL = "model"


# == parts ========================================================================================


class TextPart(BaseModel):
    text: str


class DataPart(BaseModel):
    """Inline binary content — a document or an image.

    ``data`` is decoded bytes: base64 is a wire concern, and two representations of the same thing
    do not belong in the canonical model.
    """

    media_type: str
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)


class ToolCallPart(BaseModel):
    """The model asking for a function to be run — **by the caller, never by us** (`FRD-131`).

    Nothing here is executed (`ADR-0013`): the call is forwarded, and the caller sends the outcome
    back as a :class:`ToolResultPart`. ``id`` correlates the two; one is generated where a provider
    supplies none, because Anthropic and OpenAI require a result to name its call.
    """

    id: str
    name: str
    #: Parsed, not raw text: every dialect sends an object or a JSON string of one.
    arguments: dict[str, Any] = {}


class ToolResultPart(BaseModel):
    """What running a tool produced, on its way back to the model.

    **Content the model reads, which the injection filter cannot see** (`FRD-131` §8): a file, a
    fetched page, a command's output — a route into a model about to propose the next command.
    """

    call_id: str
    name: str
    content: str


CanonicalPart = TextPart | DataPart | ToolCallPart | ToolResultPart


class ToolDeclaration(BaseModel):
    """A function the caller offers, described so a model can decide to ask for it.

    ``parameters`` reuses :class:`ResponseSchema`, so it is bounded and validated like a response
    schema (`FRD-112`). Forwarded, never executed.
    """

    name: str
    description: str = ""
    parameters: ResponseSchema | None = None


# == requests =====================================================================================


class CanonicalMessage(BaseModel):
    role: Role
    parts: list[CanonicalPart] = []

    @model_validator(mode="before")
    @classmethod
    def _accept_plain_text(cls, data: Any) -> Any:
        """Allow ``CanonicalMessage(role=..., text="…")`` — text-only is still the common case."""
        if isinstance(data, dict) and "text" in data and "parts" not in data:
            data = {**data, "parts": [{"text": data.pop("text")}]}
        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def text(self) -> str:
        """What this message *says*: the text parts, concatenated.

        Excludes attachments on purpose (`FRD-110` FR-9): the injection filter and the routing
        classifier read this, so they see the prompt and **not** the document.
        """
        return "".join(part.text for part in self.parts if isinstance(part, TextPart))

    @property
    def attachments(self) -> list[DataPart]:
        return [part for part in self.parts if isinstance(part, DataPart)]

    @property
    def tool_calls(self) -> list[ToolCallPart]:
        return [part for part in self.parts if isinstance(part, ToolCallPart)]

    @property
    def tool_results(self) -> list[ToolResultPart]:
        return [part for part in self.parts if isinstance(part, ToolResultPart)]


class Thinking(BaseModel):
    """How much reasoning effort a request asks for (`FRD-111` §5.1).

    ``mode`` is one of the gateway's control words — ``disabled``, ``auto``, ``limited`` — or a
    **level word the vendor accepts** (`low`, `high`, …): a plain string, so a vendor's new word is
    not a code change. ``tokens`` is what goes upstream, and only ``limited`` has one. The
    reservation reads the model's declaration instead
    (:func:`aira_gateway.thinking.reserved_tokens`), so a level needs no invented number.
    """

    mode: str
    tokens: int | None = None


class SpeakerVoice(BaseModel):
    """One named speaker and the voice that reads their lines (`FRD-624`)."""

    speaker: str
    voice: str


class Speech(BaseModel):
    """An answer as audio instead of text (`FRD-624`): one voice, or one per named speaker.

    The voice is the provider's name for it and is not checked against a list here: the provider
    validates it, and a list kept in the gateway would go stale.
    """

    voice: str | None = None
    speakers: tuple[SpeakerVoice, ...] = ()
    language: str | None = None


class CanonicalRequest(BaseModel):
    #: Forbidden, so a misspelled field is an error rather than a setting that does nothing.
    model_config = ConfigDict(extra="forbid")

    model: str
    #: How to reach the model on its platform, from the catalogue's `addressing` (`FRD-507`).
    #: **Opaque here**: Vertex needs a region and Azure a deployment, and only the adapter for that
    #: platform looks inside. Filled by the dispatch layer; empty for a model whose name is its
    #: whole address.
    addressing: dict[str, Any] = Field(default_factory=dict)
    messages: list[CanonicalMessage]
    temperature: float | None = None
    max_output_tokens: int | None = None
    thinking: Thinking | None = None
    #: Sampling controls (`FRD-124`); see :data:`SAMPLING_CONTROLS`.
    top_p: float | None = None
    top_k: int | None = None
    seed: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    stop_sequences: tuple[str, ...] = ()
    #: A schema the answer must conform to (`FRD-112`). **Forwarded, never executed**: validating
    #: the response would run caller-supplied regexes over provider output on the hot path.
    response_schema: ResponseSchema | None = None
    #: Functions the caller offers the model (`FRD-131`), carried there and back and **never
    #: executed** (`ADR-0013`).
    tools: tuple[ToolDeclaration, ...] = ()
    #: Mark the stable prefix — tool declarations and system instruction — as cacheable
    #: (`FRD-133`). Set from the use case's configuration, never by the caller. False leaves a
    #: request byte-identical on every dialect.
    cache_prefix: bool = False
    #: Whether the use case wants the model's reasoning back (`FRD-135` FR-3). Decided by the use
    #: case: a request asking for thoughts where they are not enabled is refused by name.
    include_reasoning: bool = False
    #: `5m` or `1h` (`FRD-133`), read only with `cache_prefix`. The cheap one unless chosen.
    cache_ttl: str = "5m"
    #: Answer with speech instead of text (`FRD-624`). Only a model that declares `speech`, on a
    #: dialect that can ask for it, serves such a request.
    speech: Speech | None = None

    def last_user_text(self) -> str:
        for message in reversed(self.messages):
            if message.role is Role.USER:
                return message.text
        return self.messages[-1].text if self.messages else ""

    @property
    def attachments(self) -> list[DataPart]:
        """Every attachment in the request, in order."""
        return [part for message in self.messages for part in message.attachments]

    @property
    def media_types(self) -> frozenset[str]:
        """The distinct media types this request carries. What a model must be able to read."""
        return frozenset(part.media_type for part in self.attachments)

    @property
    def sampling_requested(self) -> frozenset[str]:
        """Which sampling controls this request actually sets.

        Only what was *asked for*: a dialect without `top_k` must not refuse a request that never
        mentioned it. An empty `stop_sequences` counts as unset.
        """
        return frozenset(
            name
            for name in SAMPLING_CONTROLS
            if (value := getattr(self, name)) is not None and value != ()
        )

    @property
    def is_empty(self) -> bool:
        """Whether this request asks anything at all.

        An empty request would be served and billed for nothing — the argument `FRD-113` FR-7 makes
        for embeddings. Whitespace counts as empty. **A tool result counts as content** (`FRD-131`):
        it is the ordinary middle turn of an agent conversation.
        """
        return (
            not any(message.text.strip() for message in self.messages)
            and not self.attachments
            and not any(message.tool_results for message in self.messages)
        )


class CanonicalEmbeddingRequest(BaseModel):
    """One embedding call, however many texts it carries (`FRD-113` §5.1).

    A single text is a list of one: two code paths is how a batch ends up metered as one request.
    """

    model: str
    #: Platform addressing, as on :class:`CanonicalRequest`; embedding uses the same URL shape.
    addressing: dict[str, Any] = Field(default_factory=dict)
    texts: list[str]
    #: What the vectors are optimised for. Validated against the model: the wrong one produces
    #: vectors that work and retrieve measurably worse.
    task_type: str | None = None
    dimensions: int | None = None

    @property
    def size(self) -> int:
        return len(self.texts)


class EmbeddingVectors(list[list[float]]):
    """Vectors, and what the adapter could say about producing them.

    A list, so an adapter with nothing to report returns a plain one. ``served_region`` is where the
    call was answered — the audit row's residency evidence (`FRD-115` FR-10); ``input_tokens`` is
    what it cost, so it can be priced (`FRD-403`). ``None`` means not reported, never zero.
    """

    def __init__(
        self,
        vectors: Iterable[list[float]] = (),
        *,
        served_region: str = "",
        input_tokens: int | None = None,
    ) -> None:
        super().__init__(vectors)
        self.served_region = served_region
        self.input_tokens = input_tokens


# == responses ====================================================================================


class CanonicalUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int

    #: **Of which** was served from a provider-side prompt cache, and **of which** was written into
    #: one (`FRD-133`). Subsets of `prompt_tokens`, never additions, so every figure built on it
    #: keeps its meaning; apart because they are priced apart (a read 0.1x base input, a write 1.25x
    #: or 2x). Zero also on a provider that reports nothing.
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0

    #: **Of which** the model spent thinking (`FRD-135`), a subset of `completion_tokens`. Counted
    #: unconditionally: an installation decides whether to *see* reasoning, not whether it is
    #: *charged* for it — providers bill thinking at the output rate.
    reasoning_tokens: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def uncached_input_tokens(self) -> int:
        """Input billed at the ordinary rate. Clamped at zero, so a provider reporting more cache
        tokens than input tokens cannot produce a negative charge."""
        return max(0, self.prompt_tokens - self.cached_input_tokens - self.cache_write_tokens)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class CanonicalResponse(BaseModel):
    model: str
    text: str
    #: The model's reasoning, when the use case asked for it and the provider returned any
    #: (`FRD-135`). **Apart from `text`**: joined in, the caller would read reasoning as the answer.
    reasoning: str = ""
    finish_reason: str = "stop"
    usage: CanonicalUsage
    #: What the model asked to have run (`FRD-131`). A turn may carry several, and text as well.
    tool_calls: tuple[ToolCallPart, ...] = ()
    #: **Where this answer was actually produced** (`FRD-609`, `FRD-115` FR-10). A catalogued
    #: model may be tried in several regions in order, so the configuration is no evidence of
    #: where one request went. Empty on dialects with one place.
    served_region: str = ""
    #: The answer as speech (`FRD-624`): decoded audio and the media type the provider named.
    audio: DataPart | None = None


class CanonicalChunk(BaseModel):
    """A streaming delta. The final chunk carries ``finish_reason`` and ``usage``.

    ``tool_calls`` appears on the chunk that **completes** them, never in fragments (`FRD-131`
    FR-6): half-formed calls are of no use to a client.
    """

    text_delta: str
    finish_reason: str | None = None
    usage: CanonicalUsage | None = None
    tool_calls: tuple[ToolCallPart, ...] = ()
    #: A piece of the spoken answer, in the order it was produced (`FRD-624`).
    audio_delta: DataPart | None = None
