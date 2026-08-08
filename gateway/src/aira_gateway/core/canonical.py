"""Provider-agnostic canonical request/response schema (FRD-100, FRD-110).

Every API surface (Gemini now, KIRA per `ADR-0010`) maps to/from these models, and upstream
providers speak only canonical. This is the single point the whole gateway agrees on.

A message is an **ordered list of parts**: text, and inline binary data with a declared media type.
Order is preserved end to end, because "this image, then this question" and "this question, then
this image" are different prompts.

``text=`` still constructs a message and ``.text`` still reads one — the great majority of the code
legitimately wants "what does this message say", and keeping that working is what made `FRD-110` a
change to one file rather than to twenty. It is also the one thing to be careful about: ``.text``
used to be *total* and is now **lossy**. Anything that decides or persists on it must be reviewed
rather than merely compiled, which is what `FRD-110` FR-9 (the pipeline's blind spot) is about.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, computed_field, model_validator

from aira_common.models import ThinkingMode
from aira_gateway.core.schema import ResponseSchema


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    MODEL = "model"


class TextPart(BaseModel):
    text: str


class DataPart(BaseModel):
    """Inline binary content — a document or an image.

    ``data`` is decoded bytes: base64 is a wire concern and does not belong in the canonical
    model, where it would invite two representations of the same thing.
    """

    media_type: str
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)


class ToolCallPart(BaseModel):
    """The model asking for a function to be run — **by the caller, never by us** (`FRD-131`).

    Nothing here is executed. `ADR-0013` allows a declaration to be carried through and forbids
    running anything, and this part is the model's half of that: a name and arguments, forwarded to
    whoever asked. The caller decides whether to run it, runs it on their own machine, and sends
    the outcome back as a :class:`ToolResultPart` in the next turn.

    ``id`` is the provider's correlation handle. Anthropic and OpenAI both require the result to
    name the call it answers, and Gemini matches by function name instead — so an id is generated
    where a provider does not supply one, rather than leaving the other two unable to reply.
    """

    id: str
    name: str
    #: Parsed, not raw text. Every dialect either sends an object or a JSON string of one, and
    #: keeping both shapes would push that difference into every consumer.
    arguments: dict[str, Any] = {}


class ToolResultPart(BaseModel):
    """What running it produced, on its way back to the model.

    **This is content the model reads**, and the injection filter cannot see it (`FRD-131` §8):
    a file, a fetched page, a command's output — each is a route into a model that is about to
    propose the next command. Named here so the risk sits beside the type that carries it.
    """

    call_id: str
    name: str
    content: str


CanonicalPart = TextPart | DataPart | ToolCallPart | ToolResultPart


class ToolDeclaration(BaseModel):
    """A function the caller is offering, described so a model can decide to ask for it.

    ``parameters`` reuses :class:`ResponseSchema` rather than taking a free-form dict, for the same
    three reasons `FRD-112` gives: the bounds need something to count, an unknown field becomes an
    error naming the field at our boundary, and both surfaces map onto one model instead of each
    inventing their own. It is likewise **forwarded, never executed**.
    """

    name: str
    description: str = ""
    parameters: ResponseSchema | None = None


class CanonicalMessage(BaseModel):
    role: Role
    parts: list[CanonicalPart] = []

    @model_validator(mode="before")
    @classmethod
    def _accept_plain_text(cls, data: Any) -> Any:
        """Allow ``CanonicalMessage(role=..., text="…")``.

        Not nostalgia: a text-only message is still the overwhelmingly common case, and a
        constructor that forced every caller to wrap one string in a list would add ceremony to
        the path that matters most while changing nothing about it.
        """
        if isinstance(data, dict) and "text" in data and "parts" not in data:
            data = {**data, "parts": [{"text": data.pop("text")}]}
        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def text(self) -> str:
        """What this message *says* — the text parts, concatenated.

        Deliberately excludes attachments, and that exclusion is the whole point of `FRD-110`
        FR-9: the injection filter and the routing classifier read this, so they see the prompt
        and **not** the document. A prompt injection inside a PDF is invisible to them, which is
        stated in the pipeline builder rather than left to be discovered.
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

    ``mode`` plus an optional token count, taken from the predecessor's vocabulary — and it turned
    out to cover three vendors it was not written for: Google takes a budget, Anthropic a budget
    drawn from ``max_tokens``, Azure an abstract effort level with no budget at all.

    A request carries the **caller's** setting until :mod:`aira_gateway.thinking` resolves it
    against the model that will serve it; after that ``tokens`` holds the number actually sent,
    which is also the number the pre-dispatch reservation is made against. Those two must be the
    same figure or the budget is reserving for a request that was never made.
    """

    mode: ThinkingMode
    tokens: int | None = None


#: The sampling controls a request can carry beyond `temperature`, named canonically.
#:
#: They are listed rather than left implicit because **no dialect expresses all of them**, and the
#: difference has to be answerable per candidate: OpenAI has no `top_k`, Anthropic has no `seed`
#: and no penalties. `ADR-0011` rule 3 in its usual form — a flag says *whether*, the dialect says
#: *how* — with the twist that here the honest answer is sometimes "it cannot", and that has to be
#: a refusal rather than a dropped field.
SAMPLING_CONTROLS = (
    "top_p",
    "top_k",
    "seed",
    "presence_penalty",
    "frequency_penalty",
    "stop_sequences",
)


class CanonicalRequest(BaseModel):
    #: A misspelled field here would be accepted and do nothing, which is the same failure this
    #: whole feature is about, one layer in — where it would be found by nobody.
    model_config = ConfigDict(extra="forbid")

    model: str
    messages: list[CanonicalMessage]
    temperature: float | None = None
    max_output_tokens: int | None = None
    thinking: Thinking | None = None
    #: Sampling controls (`FRD-124`). Every one of these changes the answer, and every one of them
    #: was previously accepted at the surface and thrown away before dispatch — a caller who set a
    #: `seed` for reproducibility got a 200 and a different answer each time, with nothing to say
    #: why.
    top_p: float | None = None
    top_k: int | None = None
    seed: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    stop_sequences: tuple[str, ...] = ()
    #: A schema the answer must conform to (`FRD-112`). Parsed and bounded at the surface, then
    #: **forwarded, never executed** — re-validating the response would mean running
    #: caller-supplied regexes over provider output on the hot path.
    response_schema: ResponseSchema | None = None
    #: Functions the caller is offering the model (`FRD-131`). Carried to the provider and back,
    #: **never executed** (`ADR-0013`). Empty is the ordinary case and stays free of any cost: a
    #: request that declares nothing behaves exactly as it did before this field existed.
    tools: tuple[ToolDeclaration, ...] = ()

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

        Only what was *asked for* — a dialect that cannot express `top_k` must not refuse a request
        that never mentioned it. An empty `stop_sequences` counts as unset for the same reason.
        """
        return frozenset(
            name
            for name in SAMPLING_CONTROLS
            if (value := getattr(self, name)) is not None and value != ()
        )

    @property
    def is_empty(self) -> bool:
        """Whether this request asks anything at all.

        `FRD-113` FR-7 already refuses an empty embedding input, and names the reason: it prevents
        a class of accidental **no-op billing**. The same argument holds for generation and was
        simply never applied — a request whose parts are all empty was served, charged, and
        answered with whatever a model says to nothing. Found by sending `parts: []`.

        Whitespace counts as empty on purpose: a caller whose template rendered to a newline has
        the same bug as one that rendered to nothing.

        **A tool result counts as content** (`FRD-131`). The second turn of an assistant's exchange
        is often nothing but "here is what `read_file` returned" — no prose at all — and judging
        that "asks nothing" would refuse the ordinary middle of every agent conversation.
        """
        return (
            not any(message.text.strip() for message in self.messages)
            and not self.attachments
            and not any(message.tool_results for message in self.messages)
        )


class CanonicalEmbeddingRequest(BaseModel):
    """One embedding call, however many texts it carries (`FRD-113` §5.1).

    A single text is a list of one. Two code paths — one for a string, one for a list — is how a
    batch ends up metered as a single request, and a batch that costs one token of a rate limit is
    that limit with a hole in it.
    """

    model: str
    texts: list[str]
    #: What the vectors are optimised for. Indexing a corpus with ``RETRIEVAL_QUERY`` instead of
    #: ``RETRIEVAL_DOCUMENT`` produces vectors that work, sit in the right space, and retrieve
    #: measurably worse — which is why this is an enum validated against the model, not a string.
    task_type: str | None = None
    dimensions: int | None = None

    @property
    def size(self) -> int:
        return len(self.texts)


class CanonicalUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class CanonicalResponse(BaseModel):
    model: str
    text: str
    finish_reason: str = "stop"
    usage: CanonicalUsage
    #: What the model asked to have run (`FRD-131`). A turn can carry text, tool calls, or both —
    #: several at once, because all three vendors can return more than one and a single-call field
    #: would need replacing the first time one did.
    tool_calls: tuple[ToolCallPart, ...] = ()


class CanonicalChunk(BaseModel):
    """A streaming delta. The final chunk carries ``finish_reason`` and ``usage``.

    ``tool_calls`` appears on the chunk that **completes** them, never in pieces. The OpenAI dialect
    streams a call's arguments fragmented across chunks (`FRD-131` FR-6), and a mapper that passed
    each fragment on would emit several half-formed calls that no client could use.
    """

    text_delta: str
    finish_reason: str | None = None
    usage: CanonicalUsage | None = None
    tool_calls: tuple[ToolCallPart, ...] = ()
