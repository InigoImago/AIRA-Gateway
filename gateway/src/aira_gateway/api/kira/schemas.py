"""The predecessor's wire shapes (`FRD-107`).

Field names are the predecessor's. Most are snake_case; the two it spells in camelCase —
`maxTokens` and `responseSchema` (`FRD-107` FR-2) — carry an alias, with ``populate_by_name`` so the
snake_case form is accepted too. Only those two accept a second spelling.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aira_gateway.core.canonical import MAX_MESSAGES, too_long_a_conversation
from aira_gateway.embedding import TASK_TYPES

#: Response shapes: aliases accepted, extras ignored.
_ALIASED = ConfigDict(populate_by_name=True, extra="ignore")

#: Request shapes **accept** what they do not model, and **name** it (`FRD-124` §5.6).
#:
#: A compatibility surface must accept the predecessor's traffic, and its clients send fields nobody
#: here models; refusing them failed every call over fields that change no answer. Nothing is
#: ignored silently: `ignored_fields` lists the extras and the surface names them in the
#: `X-AIRA-Unmodelled-Fields` header (`headers.note_unmodelled`). Typed fields are validated as
#: before, and the Gemini surface keeps `FRD-124`'s strict rule — that is Google's contract.
_TOLERANT_ALIASED = ConfigDict(populate_by_name=True, extra="allow")

#: The predecessor's numeric model handle: any whole number, as the predecessor types it. One no
#: catalogue entry can hold — zero, negative, past the `INTEGER` column — is `MODEL_NOT_FOUND` like
#: any other id nothing has, decided before the catalogue is asked (`routes._resolve_model`): an
#: unbounded value reached Postgres as `NumericValueOutOfRange`, a 500 for a number the caller
#: chose.
ModelId = int

#: The ids a catalogue entry can carry.
MODEL_IDS = range(1, 2_147_483_648)

#: The predecessor's health vocabulary: a title-cased string, not a boolean.
HealthState = Literal["Healthy", "Unhealthy"]


def _normalise(name: str) -> str:
    return name.replace("_", "").replace("-", "").lower()


def _entries(content: Any) -> int:
    """How many parts a raw content holds — before anything validated it, so a content that is not
    an object, or parts that are not a list, count for nothing and are the validation's to
    refuse."""
    parts = content.get("parts") if isinstance(content, dict) else None
    return len(parts) if isinstance(parts, list) else 0


class TolerantRequest(BaseModel):
    """Accepts what it does not model — **except a near-miss of something it does**.

    An unknown field is accepted and named (`_TOLERANT_ALIASED`). A field that differs from a
    modelled one only by case or punctuation — `conversationHistory` for `conversation_history` —
    is refused, naming the spelling this surface takes: accepting it would answer *without* what
    was sent, a wrong answer rather than a missing feature.
    """

    @model_validator(mode="after")
    def _refuse_near_misses(self) -> TolerantRequest:
        known = {
            _normalise(name): (field.alias or name)
            for name, field in type(self).model_fields.items()
        }
        for field in type(self).model_fields.values():
            if field.alias:
                known[_normalise(field.alias)] = field.alias
        for sent in self.model_extra or {}:
            match = known.get(_normalise(str(sent)))
            if match and str(sent) != match:
                raise ValueError(
                    f"'{sent}' is not a field of this API, and it differs from '{match}' only in "
                    "spelling. Accepting it would answer without what you sent — send it as "
                    f"'{match}'."
                )
        return self


def ignored_values(request: ChatRequest) -> tuple[str, ...]:
    """What a chat request sent in a modelled field and is **not used**, as the owner decided for
    this surface (`FRD-146` FR-14): ``thinking.tokens`` beside a mode other than ``limited``, and an
    attachment in ``system_instruction`` — both ignored by the predecessor. Named like an unmodelled
    field, so an operator can still see them."""
    names: list[str] = []
    if (
        request.thinking is not None
        and request.thinking.tokens is not None
        and request.thinking.mode.strip().lower() != "limited"
    ):
        names.append("thinking.tokens")
    if request.system_instruction is not None:
        names.extend(
            f"system_instruction.parts.{index}"
            for index, part in enumerate(request.system_instruction.parts)
            if "text" not in part
        )
    return tuple(names)


def ignored_fields(*models: BaseModel | None) -> tuple[str, ...]:
    """Every field a caller sent that this surface does not model, in order, deduplicated.

    What keeps tolerance from being the silent drop `FRD-124` was written against: an operator can
    see which fields a client sends that this surface does not act on.
    """
    seen: dict[str, None] = {}
    for model in models:
        if model is None:
            continue
        for name in model.model_extra or {}:
            seen.setdefault(str(name), None)
        for value in model.__dict__.values():
            if isinstance(value, BaseModel):
                for name in ignored_fields(value):
                    seen.setdefault(name, None)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, BaseModel):
                        for name in ignored_fields(item):
                            seen.setdefault(name, None)
    return tuple(seen)


# -- requests --------------------------------------------------------------------------------------


class TextPart(TolerantRequest):
    model_config = _TOLERANT_ALIASED
    text: str


class RequestContent(TolerantRequest):
    model_config = _TOLERANT_ALIASED
    #: Plain dicts, so a part can be text, an attachment (`mime_type`/`mimeType` + `data`,
    #: `FRD-110`), or one of the two function parts (`FRD-146` FR-2), told apart by ``type``; the
    #: mapper reads them (`mapping._parts`) and the validator below is the contract.
    parts: list[dict[str, Any]]

    @model_validator(mode="after")
    def _parts_are_one_kind_or_the_other(self) -> RequestContent:
        for index, part in enumerate(self.parts):
            kind = part.get("type")
            if kind is not None and not isinstance(kind, str):
                # Asked first: the checks below look the word up in a set, which cannot hold a list
                # or an object — the caller's value would be a server error rather than their 422.
                raise ValueError(
                    f"parts[{index}]: 'type' must be a string, not {type(kind).__name__}"
                )
            if kind is None:
                # The predecessor defaults ``type`` on a function part, so a part is read by its
                # fields. Written back, so everything downstream reads one field.
                kind = _function_kind(part)
                if kind is not None:
                    part["type"] = kind
            if kind in FUNCTION_PART_TYPES:
                _check_function_part(index, str(kind), part)
                continue
            if kind not in (None, "text"):
                raise ValueError(
                    f"parts[{index}]: 'type' is '{kind}'; a part is text, an attachment, "
                    f"or one of {sorted(FUNCTION_PART_TYPES)}"
                )
            has_text = "text" in part
            has_data = "mime_type" in part or "mimeType" in part
            if has_text == has_data:
                raise ValueError(
                    f"parts[{index}]: a part carries either 'text' or 'mime_type' + 'data'"
                )
            # A text part carries text. A non-string would be converted (`null` → "None") and
            # answered with a 200 — a silently transformed value, which `FRD-124` forbids.
            if has_text and not isinstance(part["text"], str):
                raise ValueError(
                    f"parts[{index}]: 'text' must be a string, not "
                    f"{type(part['text']).__name__}. A non-string would be converted and the "
                    "model would answer about the conversion."
                )
            # And an attachment's fields, for the same reason: `"data": null` became "None", which
            # is valid base64, and the model was handed three bytes of nothing.
            for field in ("mime_type", "mimeType", "data"):
                if has_data and field in part and not isinstance(part[field], str):
                    raise ValueError(
                        f"parts[{index}]: '{field}' must be a string, not "
                        f"{type(part[field]).__name__}. A non-string would be converted, and the "
                        "model would read the conversion as the attachment."
                    )
        return self

    def kinds(self) -> frozenset[str]:
        """Which function part types this content carries."""
        return frozenset(
            str(part["type"]) for part in self.parts if part.get("type") in FUNCTION_PART_TYPES
        )


#: A function the model asks to have run, and what running it returned (`FRD-146` FR-2).
FUNCTION_CALL = "function_call"
FUNCTION_RESULT = "function_result"
FUNCTION_PART_TYPES = frozenset({FUNCTION_CALL, FUNCTION_RESULT})

#: The fields a part carries in this contract — a text, an attachment or one of the two function
#: parts. A part is a plain mapping its validator reads (`RequestContent`), so the vocabulary is
#: named here, for what has to tell the contract's names from the caller's (`log_shape`).
PART_FIELDS = frozenset(
    {
        "type",
        "text",
        "mime_type",
        "mimeType",
        "data",
        "id",
        "name",
        "arguments",
        "opaque_metadata",
        "call_id",
        "result",
        "error",
    }
)


def _function_kind(part: dict[str, Any]) -> str | None:
    """Which function part an untyped part is, by its fields; ``None`` for text or an attachment.

    A result is the part that names a call or carries a result; a call is a part with a name and
    neither text nor an attachment. Whether it stands where it may is the role's question
    (`ConversationContent`), so a call in a user's turn is refused there, by name.
    """
    if "text" in part or "mime_type" in part or "mimeType" in part:
        return None
    if "call_id" in part or "result" in part:
        return FUNCTION_RESULT
    if "name" in part:
        return FUNCTION_CALL
    return None


def _check_function_part(index: int, kind: str, part: dict[str, Any]) -> None:
    """A function part's fields, typed: a value that is not what the field holds would be
    converted, and the model told about the conversion, which `FRD-124` forbids.

    Only the *types* are the parser's: whether a name is a function name, whether a result answers
    a call and whether ids repeat are the contract's `INVALID_FUNCTION_RESPONSE`, decided in the
    mapper over the whole conversation.
    """
    if kind == FUNCTION_CALL:
        call_id = part.get("id")
        if call_id is not None and (not isinstance(call_id, str) or not call_id.strip()):
            raise ValueError(f"parts[{index}]: a {kind} part's 'id' is a non-empty string")
        if not isinstance(part.get("name"), str):
            raise ValueError(f"parts[{index}]: a {kind} part needs 'name' as a string")
        if not isinstance(part.get("arguments", {}), dict):
            raise ValueError(f"parts[{index}]: 'arguments' must be an object")
        metadata = part.get("opaque_metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError(f"parts[{index}]: 'opaque_metadata' must be an object")
        return
    call_id = part.get("call_id")
    if not isinstance(call_id, str) or not call_id.strip():
        raise ValueError(f"parts[{index}]: a {kind} part needs 'call_id' as a non-empty string")
    name = part.get("name")
    if name is not None and not isinstance(name, str):
        raise ValueError(f"parts[{index}]: a {kind} part's 'name' is a string")
    if "result" not in part:
        raise ValueError(f"parts[{index}]: a {kind} part needs 'result'")
    error = part.get("error")
    if error is not None and not isinstance(error, dict):
        raise ValueError(f"parts[{index}]: 'error' must be an object")


class ConversationContent(TolerantRequest):
    model_config = _TOLERANT_ALIASED
    content: RequestContent
    role: Literal["user", "model"]

    @model_validator(mode="after")
    def _parts_belong_to_the_role(self) -> ConversationContent:
        """A call is the model's and a result is the caller's (`FRD-146` FR-2), as the predecessor
        types a model turn and a user turn."""
        wrong = FUNCTION_RESULT if self.role == "model" else FUNCTION_CALL
        if wrong in self.content.kinds():
            raise ValueError(f"a '{self.role}' turn cannot carry a {wrong} part")
        return self


class ToolSpec(TolerantRequest):
    """A function offered to the model (`FRD-146` FR-1), every field the predecessor requires.

    A missing or empty field is the parser's `VALIDATION_ERROR`, as there; what makes a filled one
    acceptable — a name, a description that says something, a schema the predecessor would take —
    is the mapper's `INVALID_TOOL_CONFIGURATION`.
    """

    model_config = _TOLERANT_ALIASED
    type: Literal["function"] = "function"
    name: Annotated[str, Field(min_length=1, max_length=64)]
    description: Annotated[str, Field(min_length=1)]
    parameters: dict[str, Any]


class ThinkingSetting(TolerantRequest):
    model_config = _TOLERANT_ALIASED
    mode: str
    tokens: int | None = None


class ChatRequest(TolerantRequest):
    model_config = _TOLERANT_ALIASED

    request: RequestContent
    model_id: ModelId
    system_instruction: RequestContent | None = None
    conversation_history: list[ConversationContent] | None = None
    max_tokens: int | None = Field(default=None, alias="maxTokens")
    #: Bounded as the predecessor bounds it, so a value outside is its `422` naming the field. The
    #: shared layer refuses the same range on every surface (`SAMPLING_BOUNDS`) in its own status.
    temperature: float = Field(default=1.0, ge=0.0, le=2.0)
    #: Validated against what the model declares (`FRD-111`); refusals carry the predecessor's own
    #: codes, so a migrating client's error handling switches on the same strings.
    thinking: ThinkingSetting | None = None
    response_schema: dict[str, Any] | None = Field(default=None, alias="responseSchema")
    tools: list[ToolSpec] | None = None

    @model_validator(mode="before")
    @classmethod
    def _counted_before_validated(cls, values: Any) -> Any:
        """The turns and their parts, counted on the raw body before any of them is validated —
        the bound shared with the Gemini surface (`core.canonical`), refused as this contract's
        `VALIDATION_ERROR`. Validating them first is the cost the bound is there to refuse."""
        if not isinstance(values, dict):
            return values
        history = values.get("conversation_history")
        turns: list[Any] = history if isinstance(history, list) else []
        own = [values.get("request"), values.get("system_instruction")]
        messages = len(turns) + sum(content is not None for content in own)
        # Not counted past the turns' own bound: the refusal is decided, and the count would walk
        # the very list it refuses.
        parts = (
            0
            if messages > MAX_MESSAGES
            else sum(_entries(content) for content in own)
            + sum(_entries(turn.get("content")) for turn in turns if isinstance(turn, dict))
        )
        problem = too_long_a_conversation(messages, parts)
        if problem is not None:
            raise ValueError(problem)
        return values

    @model_validator(mode="after")
    def _function_parts_where_they_belong(self) -> ChatRequest:
        """The request is the caller's turn and the system instruction nobody's: a call in either
        is a turn written by the wrong party (`FRD-146` FR-2)."""
        if FUNCTION_CALL in self.request.kinds():
            raise ValueError(f"the request cannot carry a {FUNCTION_CALL} part")
        if self.system_instruction is not None and self.system_instruction.kinds():
            raise ValueError("the system instruction cannot carry function parts")
        return self


class EmbeddingRequest(TolerantRequest):
    model_config = _TOLERANT_ALIASED

    text: str | list[str]
    model_id: ModelId
    task_type: str | None = None

    @field_validator("task_type")
    @classmethod
    def _one_of_the_eight(cls, value: str | None) -> str | None:
        """The predecessor types the field as its enum, so a value outside the eight is its
        `VALIDATION_ERROR`. Whether the *model* takes a known one is the catalogue's question and
        keeps its own code (`embedding.INVALID_EMBEDDING_TASK_TYPE`)."""
        if value is None:
            return None
        normalised = value.strip().upper()
        if normalised not in TASK_TYPES:
            raise ValueError(
                f"'{value}' is not an embedding task type. Known: {sorted(TASK_TYPES)}."
            )
        return normalised


#: A text the predecessor requires and refuses empty.
FilledText = Annotated[str, Field(min_length=1)]


class RerankRecordIn(TolerantRequest):
    """One record to rank, every field filled, as the predecessor requires (`FRD-147` FR-2)."""

    model_config = _TOLERANT_ALIASED
    id: FilledText
    title: FilledText
    content: FilledText


class RerankRequest(TolerantRequest):
    model_config = _TOLERANT_ALIASED

    model_id: ModelId
    query: FilledText
    records: Annotated[list[RerankRecordIn], Field(min_length=1)]


# -- responses -------------------------------------------------------------------------------------


class FunctionCallPart(BaseModel):
    """A function call in an answer, as the predecessor returns it (`FRD-146` FR-4)."""

    type: Literal["function_call"] = "function_call"
    id: str
    name: str
    arguments: dict[str, Any]
    #: The model's proof of the reasoning behind the call, carried there and back unread — a
    #: thinking model refuses its own call replayed without it. Always present, ``null`` when
    #: there is none, as the predecessor writes it.
    opaque_metadata: dict[str, Any] | None = None


class RerankedRecord(BaseModel):
    """One record of the answer, with its score (`FRD-147` FR-3)."""

    id: str
    score: float
    title: str | None = None
    content: str | None = None


class RerankResponse(BaseModel):
    """The records in the provider's order."""

    records: list[RerankedRecord]


class UsageDataDto(BaseModel):
    token_input: int
    token_output: int


class ChatResponse(BaseModel):
    parts: list[TextPart | FunctionCallPart]
    usage_data: UsageDataDto | None = None


class EmbeddingResponse(BaseModel):
    """One text in, one vector out — the shape the contract documents."""

    vector: list[float]


class ThinkingConfig(BaseModel):
    model_config = _ALIASED
    mode: list[str] = []
    minTokens: int | None = None
    maxTokens: int | None = None
    defaultThinking: ThinkingSetting | None = None


class KiModel(BaseModel):
    """One entry of ``GET /models``. Chat and embedding models share the base and differ in the
    optional half, which is how the predecessor's polymorphic array is shaped."""

    model_config = _ALIASED

    id: int
    name: str
    provider: str
    capabilities: list[str]
    deprecated: bool = False
    max_output_tokens: int | None = None
    thinkingConfig: ThinkingConfig | None = None
    embedding_dimensions: int | None = None
    task_types: list[str] | None = None
    supports_aggregation: bool | None = None


class HealthCheck(BaseModel):
    """One entity of `GET /health`, as the predecessor's `health_check_models.py` defines it."""

    model_config = _ALIASED

    service: str
    status: HealthState
    #: Seconds the **last** background probe took (`FRD-117` §5.2 — probing inline would make
    #: readiness as slow as the slowest upstream). Real rather than a fabricated 0.0.
    time_taken: float
    tags: list[str]


class HealthResponse(BaseModel):
    model_config = _ALIASED

    status: HealthState
    #: How long *this* call took — small, because the verdicts are already in memory.
    total_time_taken: float
    entities: list[HealthCheck]


class GitInfo(BaseModel):
    commit: str | None = None
    commitShort: str | None = None
    branch: str | None = None
    stage: str | None = None


class VersionInfo(BaseModel):
    model_config = _ALIASED
    buildNumber: int | None = None
    buildTime: str | None = None
    git: GitInfo | None = None


class KiUsageRow(BaseModel):
    """One row of `/ki-usage`, in the column order of its CSV."""

    user_id: str
    #: A plain `int`, not `ModelId`: that is the bound on what a *caller* may name, and this is what
    #: the gateway reports — `0` for a row no numeric id names (`info_routes.ki_usage`).
    model_id: int
    entry_count: int
    token_input_sum: int
    token_output_sum: int
