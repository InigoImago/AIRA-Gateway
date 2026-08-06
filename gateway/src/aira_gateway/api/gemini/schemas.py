"""Pydantic models for the Gemini (Generative Language API v1beta) wire format.

Field names intentionally match Google's camelCase wire shape.

**Requests refuse what they cannot honour (`FRD-124`).** `FRD-100` FR-7 originally had these
models ignore unknown fields, so that real Gemini clients sending extra keys were not rejected.
Measured against a running gateway, that decision cost more than it bought: of twelve fields a
Google client can legitimately send, eleven were accepted with a 200 and thrown away. A caller
who set `stopSequences` got unbounded output; who set a `seed` for reproducibility got a different
answer every time; who sent `tools` got prose instead of a function call; who sent `safetySettings`
got a governance control that was never applied. None of it was visible in the response.

Google's own API rejects unknown fields, so strictness is also the *more* compatible choice — and
for a gateway whose purpose is evidence, a field silently dropped is the one failure mode there is
no way to audit after the fact. So: known and portable → carried; known and out of scope → refused
by name, with the reason; unknown → refused, naming the field.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Request models forbid what they do not model. Response models keep ignoring extras — a provider
#: adding a field must never break a caller, which is the opposite direction and the opposite rule.
_STRICT = ConfigDict(extra="forbid")


def _refuse(values: Any, reasons: dict[str, str]) -> None:
    """Refuse the fields we know about and deliberately do not serve, saying why.

    Separate from `extra="forbid"` because "Extra inputs are not permitted" is true and useless.
    A caller who sent `tools` needs to know that this gateway does not execute tools *by design*
    (`ADR-0013`), not that they misspelled something.
    """
    if not isinstance(values, dict):
        return
    for field, reason in reasons.items():
        if values.get(field) is not None:
            raise ValueError(f"'{field}' is not served by this gateway: {reason}")


class InlineData(BaseModel):
    """Google's shape for an attachment: a media type and base64 bytes."""

    model_config = _STRICT
    mimeType: str
    data: str


#: Part shapes Google defines that AIRA does not serve. `functionCall`/`functionResponse` are the
#: same `ADR-0013` boundary as `tools`, arriving one level down — and a conversation replaying a
#: function result would previously have had that turn silently deleted from the prompt, which is
#: not a degraded answer but an answer to a different question.
_PART_NOT_SERVED = {
    "functionCall": "this gateway does not execute tools (ADR-0013)",
    "functionResponse": (
        "tool results are not carried; ignoring this part would drop a turn from the conversation "
        "and answer a different question than the one asked (ADR-0013)"
    ),
    "executableCode": "code execution is not offered (ADR-0013)",
    "codeExecutionResult": "code execution is not offered (ADR-0013)",
    "fileData": (
        "attachments are sent inline as 'inlineData'; a file reference would be dropped and the "
        "model would answer about a document it never received (FRD-110)"
    ),
}


class Part(BaseModel):
    """One part of a prompt: text **or** inline data, never both and never neither.

    Modelled as optional fields with a validator rather than a union, because Google's wire format
    is a single object shape and a caller who sends `{}` deserves an error naming the problem, not
    a union-discrimination message listing two schemas.
    """

    model_config = _STRICT
    text: str | None = None
    inlineData: InlineData | None = None

    @model_validator(mode="before")
    @classmethod
    def _refuse_unserved(cls, values: Any) -> Any:
        _refuse(values, _PART_NOT_SERVED)
        return values

    @model_validator(mode="after")
    def _exactly_one(self) -> Part:
        if (self.text is None) == (self.inlineData is None):
            raise ValueError("a part must carry either 'text' or 'inlineData', not both")
        return self


class Content(BaseModel):
    model_config = _STRICT
    role: str | None = None
    parts: list[Part]


class ThinkingConfig(BaseModel):
    """Google's own field, plus the canonical form (`FRD-111` §7).

    ``thinkingBudget`` is what Google's clients already send. ``mode``/``tokens`` is the vocabulary
    the predecessor's clients use and the only way to reach the abstract levels, whose budgets are
    per model — so both spellings are accepted and **a request carrying both is a 400**, rather
    than a silent precedence rule nobody can predict from the outside.
    """

    model_config = _STRICT
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


#: `GenerationConfig` fields Google defines that AIRA does not serve, and the reason for each.
#: Refused rather than dropped — every one of them changes the answer.
_CONFIG_NOT_SERVED = {
    "responseModalities": (
        "this gateway returns text (ADR-0013). A request for audio or images would be answered "
        "with prose and a 200"
    ),
    "speechConfig": "speech synthesis is not part of direct model access (ADR-0013)",
    "responseLogprobs": "log probabilities are not carried across the four supported dialects",
    "logprobs": "log probabilities are not carried across the four supported dialects",
    "mediaResolution": "media resolution is a Gemini-only control and would not apply uniformly",
    "enableEnhancedCivicAnswers": "a Gemini-only control that would not apply uniformly",
}


class GenerationConfig(BaseModel):
    model_config = _STRICT
    temperature: float | None = None
    maxOutputTokens: int | None = None
    thinkingConfig: ThinkingConfig | None = None
    responseMimeType: str | None = None
    responseSchema: dict[str, Any] | None = None
    # Sampling controls (`FRD-124`). Accepted here and carried to the dialect, which either
    # expresses them or refuses the candidate — never drops them.
    topP: float | None = None
    topK: int | None = None
    seed: int | None = None
    presencePenalty: float | None = None
    frequencyPenalty: float | None = None
    stopSequences: list[str] | None = None
    #: Accepted only as ``1``. Gemini can return several candidates and this gateway returns one;
    #: answering a request for three with one, under a 200, would be a silent quarter-answer.
    candidateCount: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _refuse_unserved(cls, values: Any) -> Any:
        _refuse(values, _CONFIG_NOT_SERVED)
        return values

    @model_validator(mode="after")
    def _one_candidate(self) -> GenerationConfig:
        if self.candidateCount is not None and self.candidateCount != 1:
            raise ValueError(
                f"'candidateCount' must be 1; this gateway returns one candidate, and answering a "
                f"request for {self.candidateCount} with one would look like a complete answer."
            )
        return self


#: Top-level fields Google defines that AIRA does not serve. `tools` and `cachedContent` are out
#: of scope by `ADR-0013` — direct model access, not agents and not conversation state.
#: `safetySettings` is refused for a different reason: it is a **governance** control, and one
#: that holds on one vendor and silently does not on the other three is worse than none at all.
_REQUEST_NOT_SERVED = {
    "tools": (
        "this gateway provides direct model access and does not execute tools (ADR-0013). "
        "Silently ignoring the declaration would return prose where a function call was expected"
    ),
    "toolConfig": "tool execution is out of scope (ADR-0013)",
    "cachedContent": (
        "context caching is not offered (ADR-0013 — no conversation state). Ignoring it would "
        "also mean billing at uncached rates while the caller expected cached ones"
    ),
    "safetySettings": (
        "safety thresholds are a Gemini-specific control that could not be applied to the other "
        "supported providers, and a safety setting that holds for one model and silently does not "
        "for its fallback is worse than none"
    ),
}


class GenerateContentRequest(BaseModel):
    model_config = _STRICT
    contents: list[Content] = Field(min_length=1)
    systemInstruction: Content | None = None
    generationConfig: GenerationConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def _refuse_unserved(cls, values: Any) -> Any:
        _refuse(values, _REQUEST_NOT_SERVED)
        if isinstance(values, dict) and values.get("thinkingConfig") is not None:
            # Not a Google field at this level, and an easy mistake — it was made while probing
            # this very behaviour, and the request was served with the model's default thinking
            # and a 200. `extra="forbid"` would catch it; this says what to do about it.
            raise ValueError(
                "'thinkingConfig' belongs inside 'generationConfig'. At the top level it would "
                "have been ignored, and the request answered with the model's own thinking mode."
            )
        return values


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
    model_config = _STRICT
    content: Content
    taskType: str | None = None
    outputDimensionality: int | None = None


class BatchEmbedContentsRequest(BaseModel):
    """Google's batch shape (`FRD-113` §7).

    ``model`` is the one field this surface still accepts and ignores, and it is declared rather
    than swallowed by `extra="ignore"` so that the exception is visible. The URL already named the
    model; honouring a per-entry override would let one request address models the pre-dispatch
    controls never checked."""

    model_config = _STRICT
    model: str | None = None
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
