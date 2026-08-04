"""Pydantic models for the Gemini (Generative Language API v1beta) wire format.

Field names intentionally match Google's camelCase wire shape. Models ignore unknown
fields so real Gemini clients that send extra keys are not rejected (FRD-100 FR-7).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_IGNORE = ConfigDict(extra="ignore")


class Part(BaseModel):
    model_config = _IGNORE
    text: str


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
