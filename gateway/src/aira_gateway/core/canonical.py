"""Provider-agnostic canonical request/response schema (`FRD-100`, `FRD-110`).

Every API surface maps to and from these models and every upstream speaks only canonical: this is
the single point the whole gateway agrees on (`ADR-0010`).

A message is an **ordered list of parts** — text, inline data, tool calls and tool results — and
order is kept end to end, because "this image, then this question" is a different prompt from the
reverse. ``.text`` reads what a message *says* and is **lossy**: anything that decides or persists
on it must be reviewed for what it cannot see (`FRD-110` FR-9).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from aira_gateway.core.json_schema import JsonSchemaDocument
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

#: How many functions one request may offer. Google's own maximum for a request to Gemini, and
#: OpenAI's, so no client of either can depend on more; each is a schema the model reads with
#: every turn and the reservation counts, and past this a request is a catalogue rather than a
#: conversation. Both surfaces refuse more — the KIRA one in its own code.
MAX_TOOLS = 128

#: How many turns one request may carry, history and system instruction included, and how many
#: parts all of them together. **Counted before a surface validates a single one**: a turn is the
#: most expensive thing in a body to parse, and a bound checked afterwards bounds nothing that was
#: already paid for. A long agent session sends a few thousand turns before its client compacts
#: the history; at ten thousand, every turn would have to stay under a hundred tokens for the
#: conversation to fit the largest context window on offer. Two parts a turn on average, at the
#: same bound — a call and its result, a document and its question.
MAX_MESSAGES = 10_000
MAX_MESSAGE_PARTS = 2 * MAX_MESSAGES


def too_long_a_conversation(messages: int, parts: int) -> str | None:
    """Why a request of ``messages`` turns and ``parts`` parts is refused, or ``None``: one wording
    for both surfaces, each of which counts its own wire format."""
    if messages > MAX_MESSAGES:
        return (
            f"The request carries {messages} turns, more than the {MAX_MESSAGES} one request may. "
            "A conversation that long is past every model's context window; send its recent turns."
        )
    if parts > MAX_MESSAGE_PARTS:
        return (
            f"The request carries {parts} parts across its turns, more than the "
            f"{MAX_MESSAGE_PARTS} one request may."
        )
    return None


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    MODEL = "model"


class FinishReason(StrEnum):
    """Why an answer ended — one word per event, whatever each vendor calls it (`FRD-112` FR-6).

    The vendors spell one event differently: a function call ends a Gemini answer with `STOP` and an
    Anthropic or OpenAI one with `tool_use`/`tool_calls`; a safety system stops one with `SAFETY`,
    `refusal` or `content_filter`. Every rule that reads a finish reason — is this document
    complete, what does Google's enum call it — therefore reads one of these, and every dialect maps
    its own words onto them. A word a dialect does not know becomes `OTHER`, never `STOP`: an
    unknown ending is not evidence of a complete answer.
    """

    #: The model finished: its natural end or a stop sequence.
    STOP = "stop"
    #: Cut off at the output cap (or the context window), so the answer is incomplete.
    MAX_TOKENS = "max_tokens"
    #: The model stopped to have a function run — an ordinary agent turn (`FRD-131`).
    TOOL_USE = "tool_use"
    #: A vendor's safety or policy system declined the prompt or stopped the answer.
    REFUSAL = "refusal"
    #: The model answered, but not with the document the schema asked for (`FRD-112` FR-6).
    SCHEMA_UNSATISFIED = "schema_unsatisfied"
    #: Any other ending: a recitation stop, a malformed call, a word nobody here has seen.
    OTHER = "other"


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
    #: The vendor's opaque proof of the reasoning behind this call (Gemini's `thoughtSignature`).
    #: A thinking model refuses a replayed call without the signature it issued, so it travels to
    #: the caller and back on the same call, unread. It is model state, not content: nothing here
    #: decides on it, and it is not stored with the answer.
    signature: str | None = None


class ToolResultPart(BaseModel):
    """What running a tool produced, on its way back to the model.

    **Content the model reads, which the injection filter cannot see** (`FRD-131` §8): a file, a
    fetched page, a command's output — a route into a model about to propose the next command.
    """

    call_id: str
    name: str
    content: str
    #: Media the tool produced — a screenshot, a PDF it read (`FRD-145`). Checked like any other
    #: attachment, and carried to a dialect that has a place for media in a tool result.
    attachments: tuple[DataPart, ...] = ()


CanonicalPart = TextPart | DataPart | ToolCallPart | ToolResultPart


class ToolDeclaration(BaseModel):
    """A function the caller offers, described so a model can decide to ask for it.

    ``parameters`` reuses :class:`ResponseSchema`, so it is bounded and validated like a response
    schema (`FRD-112`). Forwarded, never executed.
    """

    name: str
    description: str = ""
    parameters: ResponseSchema | None = None
    #: The same, written in JSON Schema (`FRD-143`) and carried as written. At most one of the two.
    parameters_json_schema: JsonSchemaDocument | None = None


class ToolMode(StrEnum):
    """Whether the model must, may or must not call a function (`FRD-145`) — Google's four modes.

    ``validated`` is ``auto`` with the provider guaranteeing the arguments match the declaration.
    """

    AUTO = "auto"
    ANY = "any"
    NONE = "none"
    VALIDATED = "validated"


class ToolChoice(BaseModel):
    """How a request steers the model toward its functions. Absent from a request means ``auto``.

    ``allowed`` narrows which functions a call may name, and only where a call is made at all:
    with ``any`` or ``validated``, as Google defines it.
    """

    mode: ToolMode = ToolMode.AUTO
    allowed: tuple[str, ...] = ()


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


def paired_tool_results(messages: Sequence[CanonicalMessage]) -> list[CanonicalMessage]:
    """The conversation with every tool result naming the call it answers, by that call's id.

    Gemini's wire pairs a result with its call by **name** and carries no id, so a client speaking
    it replays the model's call under one id and sends the result under another — google-genai
    builds a `functionResponse` from the name alone. Anthropic and OpenAI pair by id and refuse a
    result whose id matches no call of the model turn before it. So a result whose id names no
    open call takes the id of the earliest unanswered call **of the same name** in the latest model
    turn: Google's own rule, applied where an id is required. A result that already matches, or
    that matches no call even by name, is left as it was sent.
    """
    paired: list[CanonicalMessage] = []
    open_calls: list[ToolCallPart] = []
    for message in messages:
        if message.role is Role.MODEL:
            # A model turn answers whatever came before it; only its own calls are open now.
            open_calls = list(message.tool_calls)
            paired.append(message)
            continue
        parts: list[CanonicalPart] = []
        for part in message.parts:
            if isinstance(part, ToolResultPart):
                answered = next((call for call in open_calls if call.id == part.call_id), None)
                if answered is None:
                    answered = next((call for call in open_calls if call.name == part.name), None)
                    if answered is not None:
                        part = part.model_copy(update={"call_id": answered.id})
                if answered is not None:
                    open_calls.remove(answered)
            parts.append(part)
        paired.append(message.model_copy(update={"parts": parts}))
    return paired


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
    #: The thinking is the model's declared default, not the caller's (`FRD-152`): where a dialect
    #: cannot carry it into a turn, it is left out — the installation's choice, not a request to
    #: refuse — while a setting the caller asked for is refused by name.
    thinking_from_default: bool = False
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
    #: The same, written in JSON Schema (`FRD-143`) and carried as written. At most one of the two.
    response_json_schema: JsonSchemaDocument | None = None
    #: Functions the caller offers the model (`FRD-131`), carried there and back and **never
    #: executed** (`ADR-0013`).
    tools: tuple[ToolDeclaration, ...] = ()
    #: Whether a call is required, allowed or forbidden (`FRD-145`). ``None`` is ``auto`` and
    #: leaves the body as it was before the field existed.
    tool_choice: ToolChoice | None = None
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
        """Every attachment in the request, in order — a tool result's included, so the limits
        and the model's media types hold for those too."""
        return [
            attachment
            for message in self.messages
            for part in message.parts
            for attachment in (
                (part,)
                if isinstance(part, DataPart)
                else part.attachments
                if isinstance(part, ToolResultPart)
                else ()
            )
        ]

    @property
    def media_types(self) -> frozenset[str]:
        """The distinct media types this request carries. What a model must be able to read."""
        return frozenset(part.media_type for part in self.attachments)

    @property
    def structured(self) -> bool:
        """Whether the answer must be a document matching a schema, in either vocabulary."""
        return self.response_schema is not None or self.response_json_schema is not None

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


def wire_name(model: str, addressing: dict[str, Any] | None) -> str:
    """The name the platform knows a model by: the catalogue entry's ``addressing.model`` where the
    entry is an alias of another model (`FRD-146` FR-10), its own name otherwise.

    Only the wire carries it. Residency, price, release, approval and the audit row stay the
    entry's — so one upstream model can be two entries, each decided on its own.
    """
    named = (addressing or {}).get("model")
    return named.strip() if isinstance(named, str) and named.strip() else model


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
    #: The texts are **one** embedding — parts of one content, one vector (`FRD-146` FR-8). A
    #: dialect that cannot combine them refuses; splitting them would answer n vectors, or one of n.
    aggregate: bool = False
    #: The task type is already written into the texts, as a prompt (`FRD-146` FR-7): it is
    #: validated against the model like any other and **not sent** as a field.
    task_in_text: bool = False

    @property
    def size(self) -> int:
        return len(self.texts)


class RerankRecord(BaseModel):
    """One record to be ranked (`FRD-147`). A title, a content, or both."""

    id: str
    title: str = ""
    content: str = ""


#: How many records the provider bills as one ranking query (`FRD-147` FR-7).
RECORDS_PER_RANKING_UNIT = 100


class CanonicalRerankRequest(BaseModel):
    """Order ``records`` by how well each answers ``query`` (`FRD-147`)."""

    model: str
    #: Platform addressing, as on :class:`CanonicalRequest`: the ranking API is placed like a model.
    addressing: dict[str, Any] = Field(default_factory=dict)
    query: str
    records: list[RerankRecord]
    #: Return only the best ``top_n``; every record is ranked either way.
    top_n: int | None = None
    #: Return ids and scores only, without the records' title and content.
    ids_only: bool = False

    @property
    def units(self) -> int:
        """What the provider bills this as: one ranking query per hundred records, rounded up."""
        return max(1, -(-len(self.records) // RECORDS_PER_RANKING_UNIT))

    def texts(self) -> list[str]:
        """Everything that is sent, in order — the query, then each record's title and content —
        for the pipeline and the reservation, which read what a request says."""
        return [
            self.query,
            *(text for record in self.records for text in (record.title, record.content)),
        ]


class RankedRecord(BaseModel):
    """One record of the answer, with its score: from 0 to 1, higher is a better match."""

    id: str
    score: float
    title: str | None = None
    content: str | None = None


class RankedRecords(list[RankedRecord]):
    """The answer, in the provider's order, and where it was produced (``served_region``)."""

    def __init__(self, records: Iterable[RankedRecord] = (), *, served_region: str = "") -> None:
        super().__init__(records)
        self.served_region = served_region


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
    #: **Of which** written for an hour rather than five minutes (`FRD-133`), a subset of
    #: `cache_write_tokens`: a provider that offers both lifetimes prices the hour higher.
    long_cache_write_tokens: int = 0

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
    finish_reason: FinishReason = FinishReason.STOP
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
    """A streaming delta. The final chunk carries ``finish_reason``, and ``usage`` as it stands.

    ``usage`` may also ride earlier chunks as the running total — some dialects report it on every
    chunk — so the **last** one seen is the answer's, and a stream that breaks off still says what
    it had cost by then.

    ``tool_calls`` appears on the chunk that **completes** them, never in fragments (`FRD-131`
    FR-6): half-formed calls are of no use to a client.
    """

    text_delta: str
    #: A piece of the model's reasoning, where the use case asked for it (`FRD-135`, `FRD-145`).
    #: Apart from ``text_delta`` for the reason `CanonicalResponse.reasoning` is apart from `text`.
    reasoning_delta: str = ""
    finish_reason: FinishReason | None = None
    usage: CanonicalUsage | None = None
    tool_calls: tuple[ToolCallPart, ...] = ()
    #: A piece of the spoken answer, in the order it was produced (`FRD-624`).
    audio_delta: DataPart | None = None
    #: **Where this stream was opened**, on its first chunk (`FRD-609`, `FRD-115` FR-10) — the
    #: streamed counterpart of `CanonicalResponse.served_region`. A model catalogued in several
    #: regions is opened in the first that accepts it, so the configuration is no evidence of where
    #: the request went. ``None`` on every later chunk and on dialects with one place.
    served_region: str | None = None
