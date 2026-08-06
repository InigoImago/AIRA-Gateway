"""The predecessor's wire shapes (`kira_api.md` §2, §4).

Field names are the predecessor's, camelCase aliases included, with ``populate_by_name`` so both
spellings are accepted — exactly as §12.1 describes. A compatibility surface that required the
"nicer" spelling would not be one.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_ALIASED = ConfigDict(populate_by_name=True, extra="ignore")


class TextPart(BaseModel):
    model_config = _ALIASED
    text: str


class DataPart(BaseModel):
    """An attachment. `FRD-110` landed before this surface did, so Stage A carries documents —
    the plan had them arriving in Stage B, and refusing a capability we have would be silly."""

    model_config = _ALIASED
    mime_type: str = Field(alias="mime_type")
    data: str


class RequestContent(BaseModel):
    model_config = _ALIASED
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
    model_config = _ALIASED
    content: RequestContent
    role: Literal["user", "model"]


class ThinkingSetting(BaseModel):
    model_config = _ALIASED
    mode: str
    tokens: int | None = None


class ChatRequest(BaseModel):
    model_config = _ALIASED

    request: RequestContent
    model_id: int = Field(alias="model_id")
    system_instruction: RequestContent | None = Field(default=None, alias="system_instruction")
    conversation_history: list[ConversationContent] | None = Field(
        default=None, alias="conversation_history"
    )
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
    model_config = _ALIASED

    text: str | list[str]
    model_id: int = Field(alias="model_id")
    task_type: str | None = Field(default=None, alias="task_type")


class EmbeddingResponse(BaseModel):
    """One text in, one vector out — the predecessor's documented shape (`kira_api.md` §2.3)."""

    vector: list[float]


class BatchEmbeddingResponse(BaseModel):
    """Many texts in, one vector each, in the order submitted.

    **An extension, and deliberately a visible one.** The predecessor documents a *singular*
    ``vector`` for an input that may be a list, which reads two ways: a list yields one vector per
    text, or a list is reduced to a single vector. `FRD-113` §11 records the ambiguity and assumes
    the first — it is what the provider API offers and what a chunk-indexing consumer needs.

    Returning n vectors under the singular key would be a lie about the shape; returning only the
    first would be a silent data loss. A distinct key makes the assumption checkable by whoever
    confirms it against the running predecessor, instead of burying it.
    """

    vectors: list[list[float]]


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
    service: str
    healthy: bool
    tags: list[str]


class HealthResponse(BaseModel):
    status: Literal["HEALTHY", "UNHEALTHY"]
    checks: list[HealthCheck]


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
