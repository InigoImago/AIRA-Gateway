"""The predecessor's wire shapes (`kira_api.md` §2, §4).

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

#: Request shapes refuse what they do not model (`FRD-124`). Stage A's rule was already "an
#: unsupported field is refused by name, never ignored", and it was enforced only for the two
#: fields anybody had thought of. A migrating client that sends a field the predecessor accepted
#: and this surface does not now learns so at migration time, which is the entire point of running
#: a compatibility surface rather than hoping.
_STRICT_ALIASED = ConfigDict(populate_by_name=True, extra="forbid")


class TextPart(BaseModel):
    model_config = _STRICT_ALIASED
    text: str


# An attachment part had a class here and nothing ever built one: `parts` is deliberately a list
# of plain dicts (see below), and the mapper reads them itself. It was worse than unused —
# `extra="forbid"` on a shape accepting only `mime_type` described a surface stricter than the one
# that runs, which takes `mimeType` as well. A shape that documents a contract the server does not
# have is the unreachable-helper problem in a costume, so the validator below, which does run, is
# where the rule lives. `FRD-110`'s attachments are unaffected: Stage A has carried documents since
# the day it shipped, through `mapping._parts`.


class RequestContent(BaseModel):
    model_config = _STRICT_ALIASED
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
        return self


class ConversationContent(BaseModel):
    model_config = _STRICT_ALIASED
    content: RequestContent
    role: Literal["user", "model"]


class ThinkingSetting(BaseModel):
    model_config = _STRICT_ALIASED
    mode: str
    tokens: int | None = None


class ChatRequest(BaseModel):
    model_config = _STRICT_ALIASED

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


class EmbeddingRequest(BaseModel):
    model_config = _STRICT_ALIASED

    text: str | list[str]
    model_id: int
    task_type: str | None = None


class EmbeddingResponse(BaseModel):
    """One text in, one vector out — the predecessor's documented shape (`kira_api.md` §2.3)."""

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
    copied, and inside a list called ``checks`` where the predecessor calls it ``entities``. Every
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
