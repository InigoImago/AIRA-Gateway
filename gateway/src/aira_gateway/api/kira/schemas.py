"""The predecessor's wire shapes.

Field names are the predecessor's. Most of them are snake_case there and are spelled that way
here; the ones the predecessor spells in camelCase — `maxTokens` and `responseSchema` (`FRD-107`
FR-2) — carry an alias, with ``populate_by_name`` so the snake_case form is accepted too. A
compatibility surface that required the "nicer" spelling would not be one.

The sentence above used to say that *every* field accepted both spellings, and five fields carried
an ``alias=`` that restated their own name — which looks like a second spelling and is not one.
Nothing behaved wrongly; a reader checking whether `conversationHistory` was accepted would have
been told yes by the module and no by the server. This project has named that shape often enough:
a comment claiming a rule the system does not have. The redundant aliases are gone so the two
that remain are the two that mean something.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_ALIASED = ConfigDict(populate_by_name=True, extra="ignore")

#: Request shapes **accept** what they do not model, and **name** it (`FRD-124` §5.6).
#:
#: This was `extra="forbid"`, and the argument for it was good: an unsupported field refused by
#: name is a migrating client learning at migration time rather than hoping. What it missed is that
#: a compatibility surface's job is to accept the predecessor's traffic, and the predecessor's
#: clients send fields nobody here has heard of. Measured against a real chatbot: every call `422`,
#: over fields that change no answer.
#:
#: The rule that replaces it keeps the half that mattered — **nothing is ignored silently**. Where
#: the names go is the route's business, not this module's: `note_unmodelled` in `routes.py` puts
#: them in the `X-AIRA-Unmodelled-Fields` response header on every exit, and on the
#: `kira_request_refused` log line when the request failed for some other reason. This module only
#: has to stop refusing, and to say which fields were extra — see `ignored_fields` below.
#:
#: Typed fields are validated exactly as before: a `model_id` that is not an integer is still a
#: `422`, and `FRD-124`'s rule stands unchanged on the **Gemini** surface, which is Google's
#: contract rather than a migration path.
_TOLERANT_ALIASED = ConfigDict(populate_by_name=True, extra="allow")


def _normalise(name: str) -> str:
    return name.replace("_", "").replace("-", "").lower()


class TolerantRequest(BaseModel):
    """Accepts what it does not model — **except a near-miss of something it does**.

    Two failures, and only one of them is fixed by tolerance.

    A client sending a field this surface never heard of is a compatibility problem, and refusing
    it stops the client working for no gain: measured against a real chatbot, whose every call came
    back `422`. Those are accepted, and named on the response — see `_TOLERANT_ALIASED`.

    A client sending `conversationHistory` where this surface calls it `conversation_history` is a
    different thing entirely. Accepting that quietly answers **without the conversation** — a wrong
    answer rather than a missing feature, and the one case `FRD-124`'s rule was really protecting.
    A field whose name differs from a modelled one only by case or punctuation is refused, by name,
    with the spelling this surface takes.
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


class TextPart(TolerantRequest):
    model_config = _TOLERANT_ALIASED
    text: str


# An attachment part had a class here and nothing ever built one: `parts` is deliberately a list
# of plain dicts (see below), and the mapper reads them itself. It was worse than unused —
# `extra="forbid"` on a shape accepting only `mime_type` described a surface stricter than the one
# that runs, which takes `mimeType` as well. A shape that documents a contract the server does not
# have is the unreachable-helper problem in a costume, so the validator below, which does run, is
# where the rule lives. `FRD-110`'s attachments are unaffected: Stage A has carried documents since
# the day it shipped, through `mapping._parts`.


class RequestContent(TolerantRequest):
    model_config = _TOLERANT_ALIASED
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
            # **A text part carries text.** `parts` is a list of plain dicts so that a part can be
            # either kind, and the mapper used to hand whatever arrived to `str(...)`. Measured on
            # 2026-08-12:
            #
            #     {"text": null}      → the model was asked about the word  "None"
            #     {"text": 123}       → "123"
            #     {"text": true}      → "True"
            #     {"text": {"a": 1}}  → "{'a': 1}"      (a Python repr, on the wire)
            #
            # No error, a 200, and a fluent answer to a question nobody asked — the shape this
            # project keeps paying for, and our own `FRD-124` rule broken in our own code: a value
            # silently transformed is worse than one refused, because only the refusal is visible.
            # The predecessor types this field as a string and rejects the rest, so refusing is
            # also the *compatible* answer; that is a coincidence, and it would be right either
            # way.
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


def ignored_fields(*models: BaseModel | None) -> tuple[str, ...]:
    """Every field a caller sent that this surface does not model, in order, deduplicated.

    The half of `extra="forbid"` worth keeping: a compatibility surface accepts the predecessor's
    traffic, and **says what it did not understand**. Without this, tolerance is the silent drop
    `FRD-124` was written against; with it, an operator can see that a client is sending
    `thinkingBudget` months before anybody wonders why thinking never happens.
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


class ChatRequest(TolerantRequest):
    model_config = _TOLERANT_ALIASED

    request: RequestContent
    model_id: int
    system_instruction: RequestContent | None = None
    conversation_history: list[ConversationContent] | None = None
    max_tokens: int | None = Field(default=None, alias="maxTokens")
    temperature: float = 1.0
    #: Served since Stage B (`FRD-111`). Validated against what the model declares, and the
    #: refusals carry the predecessor's own codes so a migrating client's error handling still
    #: switches on the same strings.
    thinking: ThinkingSetting | None = None
    response_schema: dict[str, Any] | None = Field(default=None, alias="responseSchema")


class UsageDataDto(BaseModel):
    token_input: int
    token_output: int


class ChatResponse(BaseModel):
    parts: list[TextPart]
    usage_data: UsageDataDto | None = None


class EmbeddingRequest(TolerantRequest):
    model_config = _TOLERANT_ALIASED

    text: str | list[str]
    model_id: int
    task_type: str | None = None


class EmbeddingResponse(BaseModel):
    """One text in, one vector out — the shape the contract documents."""

    vector: list[float]


# `BatchEmbeddingResponse` stood here until 2026-08-12, carrying `vectors: list[list[float]]`.
#
# It existed because `FRD-113` §11 could not tell which of two readings the predecessor meant for a
# list input — one vector per text, or one vector for the lot — assumed the first, and made the
# assumption **visible on the wire** under a distinct key so that whoever checked against the real
# predecessor would notice rather than have to dig.
#
# That is exactly what happened: a comparison against the predecessor's own source confirmed the
# **second** reading. The key did its job, and its job is finished. Kept as a note rather than as a
# class, because a response model nothing returns is a shape somebody will eventually return.


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


#: The predecessor's health vocabulary is a **string**, title-cased, not a boolean and not an
#: upper-cased one. Both fields carried the wrong spelling here until 2026-08-12, which a typed
#: client cannot deserialise at all — the one failure mode a compatibility surface exists to
#: prevent.
HealthState = Literal["Healthy", "Unhealthy"]


class HealthCheck(BaseModel):
    """One entity of `GET /health` (`health_check_models.py`).

    This shipped with `FRD-107` as ``{service, healthy: bool, tags}`` — invented rather than
    copied, and inside a list called ``checks`` where the contract calls it ``entities``. Every
    field name was different and the status was a boolean where the predecessor has a string, so
    the endpoint that tells a monitoring system whether to page somebody was the *least*
    compatible thing on the surface. Found by a static comparison against the predecessor's own
    source, not by any test here: our tests assert our shape, which is exactly what a shape
    somebody invented will always pass.
    """

    model_config = _ALIASED

    service: str
    status: HealthState
    #: Seconds. The predecessor measures each check as it runs it; we probe in the background
    #: (`FRD-117` §5.2 — probing inline makes readiness as slow as the slowest upstream), so this
    #: is how long the **last** probe took. Real either way, which is the part that matters: a
    #: fabricated 0.0 would be a number somebody graphs.
    time_taken: float
    tags: list[str]


class HealthResponse(BaseModel):
    model_config = _ALIASED

    status: HealthState
    #: How long *this* call took. Small, and honestly so: the verdicts are already in memory.
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
    model_id: int
    entry_count: int
    token_input_sum: int
    token_output_sum: int
