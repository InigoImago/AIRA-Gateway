"""Pydantic models for the Gemini (Generative Language API v1beta) wire format.

Field names intentionally match Google's camelCase wire shape. Models ignore unknown
fields so real Gemini clients that send extra keys are not rejected (FRD-100 FR-7).
"""

from __future__ import annotations

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


class GenerationConfig(BaseModel):
    model_config = _IGNORE
    temperature: float | None = None
    maxOutputTokens: int | None = None


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


class ContentEmbedding(BaseModel):
    values: list[float]


class EmbedContentResponse(BaseModel):
    embedding: ContentEmbedding


class GeminiErrorDetail(BaseModel):
    code: int
    message: str
    status: str


class GeminiError(BaseModel):
    error: GeminiErrorDetail
