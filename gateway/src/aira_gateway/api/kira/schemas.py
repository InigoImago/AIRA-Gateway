"""The predecessor's wire shapes (`FRD-107`).

Field names are the predecessor's. Most are snake_case; the two it spells in camelCase —
`maxTokens` and `responseSchema` (`FRD-107` FR-2) — carry an alias, with ``populate_by_name`` so the
snake_case form is accepted too. Only those two accept a second spelling.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

#: The predecessor's numeric model handle, bounded to what the `INTEGER` column holds: an unbounded
#: value reached Postgres as `NumericValueOutOfRange`, a 500 for a number the caller chose.
ModelId = Annotated[int, Field(ge=1, le=2_147_483_647)]

#: The predecessor's health vocabulary: a title-cased string, not a boolean.
HealthState = Literal["Healthy", "Unhealthy"]


def _normalise(name: str) -> str:
    return name.replace("_", "").replace("-", "").lower()


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
    #: Plain dicts, so a part can be either text or an attachment (`mime_type`/`mimeType` + `data`,
    #: `FRD-110`); the mapper reads them (`mapping._parts`) and the validator below is the contract.
    parts: list[dict[str, Any]]

    @model_validator(mode="after")
    def _parts_are_one_kind_or_the_other(self) -> RequestContent:
        for index, part in enumerate(self.parts):
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
        return self


class ConversationContent(TolerantRequest):
    model_config = _TOLERANT_ALIASED
    content: RequestContent
    role: Literal["user", "model"]


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
    temperature: float = 1.0
    #: Validated against what the model declares (`FRD-111`); refusals carry the predecessor's own
    #: codes, so a migrating client's error handling switches on the same strings.
    thinking: ThinkingSetting | None = None
    response_schema: dict[str, Any] | None = Field(default=None, alias="responseSchema")


class EmbeddingRequest(TolerantRequest):
    model_config = _TOLERANT_ALIASED

    text: str | list[str]
    model_id: ModelId
    task_type: str | None = None


# -- responses -------------------------------------------------------------------------------------


class UsageDataDto(BaseModel):
    token_input: int
    token_output: int


class ChatResponse(BaseModel):
    parts: list[TextPart]
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
    user_id: str
    model_id: ModelId
    entry_count: int
    token_input_sum: int
    token_output_sum: int
