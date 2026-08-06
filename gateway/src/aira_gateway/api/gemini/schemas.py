"""Pydantic models for the Gemini (Generative Language API v1beta) wire format.

Field names intentionally match Google's camelCase wire shape. Models ignore unknown
fields so real Gemini clients that send extra keys are not rejected (FRD-100 FR-7).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

_IGNORE = ConfigDict(extra="ignore")


class InlineData(BaseModel):
    """Google's shape for an attachment: a media type and base64 bytes."""

    model_config = _IGNORE
    mimeType: str
    data: str


class Part(BaseModel):
    """One part of a prompt: text **or** inline data, never both and never neither.

    Modelled as optional fields with a validator rather than a union, because Google's wire format
    is a single object shape and a caller who sends `{}` deserves an error naming the problem, not
    a union-discrimination message listing two schemas.
    """

    model_config = _IGNORE
    text: str | None = None
    inlineData: InlineData | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Part:
        if (self.text is None) == (self.inlineData is None):
            raise ValueError("a part must carry either 'text' or 'inlineData', not both")
        return self


class Content(BaseModel):
    model_config = _IGNORE
    role: str | None = None
    parts: list[Part]


class ThinkingConfig(BaseModel):
    """Google's own field, plus the canonical form (`FRD-111` §7).

    ``thinkingBudget`` is what Google's clients already send. ``mode``/``tokens`` is the vocabulary
    the predecessor's clients use and the only way to reach the abstract levels, whose budgets are
    per model — so both spellings are accepted and **a request carrying both is a 400**, rather
    than a silent precedence rule nobody can predict from the outside.
    """

    model_config = _IGNORE
    thinkingBudget: int | None = None
    mode: str | None = None
    tokens: int | None = None

    @model_validator(mode="after")
    def _not_both_spellings(self) -> ThinkingConfig:
        if self.thinkingBudget is not None and (self.mode is not None or self.tokens is not None):
            raise ValueError(
                "send either 'thinkingBudget' or 'mode'/'tokens' in thinkingConfig, not both"
            )
        return self


class GenerationConfig(BaseModel):
    model_config = _IGNORE
    temperature: float | None = None
    maxOutputTokens: int | None = None
    thinkingConfig: ThinkingConfig | None = None
    responseMimeType: str | None = None
    responseSchema: dict[str, Any] | None = None


class GenerateContentRequest(BaseModel):
    model_config = _IGNORE
    contents: list[Content] = Field(min_length=1)
    systemInstruction: Content | None = None
    generationConfig: GenerationConfig | None = None


class Candidate(BaseModel):
    content: Content
    finishReason: str
    index: int


class UsageMetadata(BaseModel):
    promptTokenCount: int
    candidatesTokenCount: int
    totalTokenCount: int


class GenerateContentResponse(BaseModel):
    candidates: list[Candidate]
    usageMetadata: UsageMetadata
    modelVersion: str


class GeminiModel(BaseModel):
    name: str
    version: str
    displayName: str
    supportedGenerationMethods: list[str]
    # AIRA extensions (FRD-114 §7): a client can discover what a model may be asked to do rather
    # than reading our documentation — and, more usefully, see when nobody has declared it.
    airaCapabilities: list[str] | None = None
    airaMaxOutputTokens: int | None = None
    airaDeprecated: bool | None = None
    airaDeclared: bool | None = None


class ListModelsResponse(BaseModel):
    models: list[GeminiModel]


class EmbedContentRequest(BaseModel):
    model_config = _IGNORE
    content: Content
    taskType: str | None = None
    outputDimensionality: int | None = None


class BatchEmbedContentsRequest(BaseModel):
    """Google's batch shape (`FRD-113` §7). ``model`` on each entry is accepted and ignored:
    the URL already named it, and honouring a per-entry override would let one request address
    models the pre-dispatch controls never checked."""

    model_config = _IGNORE
    requests: list[EmbedContentRequest] = Field(min_length=1)


class ContentEmbedding(BaseModel):
    values: list[float]


class BatchEmbedContentsResponse(BaseModel):
    embeddings: list[ContentEmbedding]


class EmbedContentResponse(BaseModel):
    embedding: ContentEmbedding


class GeminiErrorDetail(BaseModel):
    code: int
    message: str
    status: str


class GeminiError(BaseModel):
    error: GeminiErrorDetail
