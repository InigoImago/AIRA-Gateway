"""Pydantic models for the Gemini (Generative Language API v1beta) wire format.

Field names match Google's camelCase wire shape.

**Requests refuse what they cannot honour (`FRD-124`).** A field silently dropped is the one failure
a gateway built for evidence cannot audit afterwards, and Google's own API rejects unknown fields
too. So: known and portable → carried; known and out of scope → refused by name, with the reason;
unknown → refused, naming the field.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Request models forbid what they do not model. Response models keep ignoring extras — a provider
#: adding a field must never break a caller, which is the opposite direction and the opposite rule.
_STRICT = ConfigDict(extra="forbid")

#: What every provider accepts as a function name, checked here so a caller gets an error naming the
#: field rather than a provider error naming nothing (`FRD-112`'s argument). `\Z`, not `$`: `$` also
#: matches before a trailing newline, and this name reaches the audit row and span attributes.
_FUNCTION_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")

# -- fields Google defines that this gateway refuses, each with the reason given to the caller -----

#: Part shapes. `functionCall`/`functionResponse` are carried (`FRD-131`): `ADR-0013` refuses
#: *executing* something on our behalf, which is what code execution asks of a provider.
_PART_NOT_SERVED = {
    "executableCode": "code execution is not offered (ADR-0013)",
    "codeExecutionResult": "code execution is not offered (ADR-0013)",
    "fileData": (
        "attachments are sent inline as 'inlineData'; a file reference would be dropped and the "
        "model would answer about a document it never received (FRD-110)"
    ),
}

#: `GenerationConfig` fields. Refused rather than dropped — every one of them changes the answer.
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

#: Top-level fields. `cachedContent` is out of scope by `ADR-0013` (no conversation state);
#: `safetySettings` is a governance control that would hold on one vendor and silently not on the
#: other three, which is worse than none.
_REQUEST_NOT_SERVED = {
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


def _refuse(values: Any, reasons: dict[str, str]) -> None:
    """Refuse the fields we know about and deliberately do not serve, saying why.

    Separate from `extra="forbid"`, whose "Extra inputs are not permitted" does not tell a caller
    that a field is refused by design (`ADR-0013`) rather than misspelled.
    """
    if not isinstance(values, dict):
        return
    for field, reason in reasons.items():
        if values.get(field) is not None:
            raise ValueError(f"'{field}' is not served by this gateway: {reason}")


# -- requests --------------------------------------------------------------------------------------


class InlineData(BaseModel):
    """Google's shape for an attachment: a media type and base64 bytes."""

    model_config = _STRICT
    mimeType: str
    data: str


class FunctionCall(BaseModel):
    """The model asking for a function to be run — by the caller (`FRD-131`)."""

    model_config = _STRICT
    name: str
    args: dict[str, Any] = {}
    #: Google matches a result to a call by name; the other dialects require an id. Accepted if
    #: sent and generated otherwise, so a conversation started here can be continued anywhere.
    id: str | None = None


class FunctionResponse(BaseModel):
    """What running it produced, on its way back to the model."""

    model_config = _STRICT
    name: str
    #: An object, as Google defines it; a plain string is refused rather than stringified.
    response: dict[str, Any] = {}
    id: str | None = None


class Part(BaseModel):
    """One part of a prompt: text, inline data, a function call or a function response — **exactly
    one of them**.

    Optional fields with a validator rather than a union: Google's wire format is one object shape,
    and a caller who sends `{}` deserves an error naming the problem, not a union-discrimination
    message listing four schemas.
    """

    model_config = _STRICT
    text: str | None = None
    inlineData: InlineData | None = None
    functionCall: FunctionCall | None = None
    functionResponse: FunctionResponse | None = None
    #: Google's marker for a reasoning part (`FRD-135`), set on the way **out** where a use case has
    #: enabled reasoning — without it the thoughts are indistinguishable from the answer.
    thought: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _refuse_unserved(cls, values: Any) -> Any:
        _refuse(values, _PART_NOT_SERVED)
        return values

    @model_validator(mode="after")
    def _exactly_one(self) -> Part:
        present = [
            name
            for name, value in (
                ("text", self.text),
                ("inlineData", self.inlineData),
                ("functionCall", self.functionCall),
                ("functionResponse", self.functionResponse),
            )
            if value is not None
        ]
        if len(present) != 1:
            raise ValueError(
                "a part must carry exactly one of 'text', 'inlineData', 'functionCall' or "
                f"'functionResponse' (got {len(present)}: {present or 'none'})"
            )
        return self


class Content(BaseModel):
    model_config = _STRICT
    role: str | None = None
    parts: list[Part]


class ThinkingConfig(BaseModel):
    """Google's own field, plus the canonical form (`FRD-111` §7).

    ``thinkingBudget`` is what Google's clients send; ``mode``/``tokens`` is the predecessor's
    vocabulary and the only way to reach the abstract levels. **A request carrying both is a 400**
    rather than a precedence rule nobody can predict from outside.

    The snake_case aliases are what the official `google-genai` SDK puts on the wire inside
    `thinkingConfig` (everything else it sends is camelCase), so refusing them would make the
    common "do not think" configuration unusable from that client.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    thinkingBudget: int | None = Field(default=None, alias="thinking_budget")
    #: Google's request to have the reasoning **returned**. Decided per use case at the surface
    #: (`FRD-135` FR-3/FR-4) and refused wherever reasoning is off — a 200 with no thoughts is the
    #: silent drop `FRD-124` is against.
    includeThoughts: bool | None = Field(default=None, alias="include_thoughts")
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
    model_config = _STRICT
    temperature: float | None = None
    maxOutputTokens: int | None = None
    thinkingConfig: ThinkingConfig | None = None
    responseMimeType: str | None = None
    responseSchema: dict[str, Any] | None = None
    # Sampling controls (`FRD-124`): carried to the dialect, which either expresses them or refuses
    # the candidate — never drops them.
    topP: float | None = None
    topK: int | None = None
    seed: int | None = None
    presencePenalty: float | None = None
    frequencyPenalty: float | None = None
    stopSequences: list[str] | None = None
    #: Accepted only as ``1``: this gateway returns one candidate, and answering a request for three
    #: with one, under a 200, would look like a complete answer.
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


class FunctionDeclaration(BaseModel):
    """A function the caller offers the model (`FRD-131`).

    ``parameters`` stays raw JSON here and is parsed by `core.schema` in the mapper — the same
    parser, bounds and error vocabulary a `responseSchema` gets.
    """

    model_config = _STRICT
    name: str
    description: str = ""
    parameters: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _usable_name(self) -> FunctionDeclaration:
        if not _FUNCTION_NAME.match(self.name):
            raise ValueError(
                f"'{self.name}' is not a usable function name. Providers accept letters, digits, "
                "'_' and '-', up to 64 characters — a name outside that is rejected downstream "
                "with a message that names neither the tool nor the field."
            )
        return self


class Tool(BaseModel):
    """Google nests declarations one level down, in a list of tools."""

    model_config = _STRICT
    functionDeclarations: list[FunctionDeclaration] = []


class FunctionCallingConfig(BaseModel):
    """How the model is steered toward calling a function — **`AUTO` only**.

    `AUTO` is the default, what happens when nothing is sent, and real clients send it on every
    request. `ANY` (must call) and `NONE` (must not) change the answer, each dialect spells them
    differently, and that mapping is not built — so they are refused by name, with that reason.
    """

    model_config = _STRICT
    mode: str = "AUTO"

    @model_validator(mode="after")
    def _only_auto(self) -> FunctionCallingConfig:
        if self.mode.strip().upper() != "AUTO":
            raise ValueError(
                f"'{self.mode}' is not served: this gateway carries tool declarations and lets the "
                "model decide ('AUTO'). Forcing or forbidding a call is expressed differently by "
                "each provider and is not implemented, so it is refused rather than accepted and "
                "quietly downgraded to 'AUTO'."
            )
        return self


class ToolConfig(BaseModel):
    model_config = _STRICT
    functionCallingConfig: FunctionCallingConfig | None = None


class GenerateContentRequest(BaseModel):
    model_config = _STRICT
    contents: list[Content] = Field(min_length=1)
    systemInstruction: Content | None = None
    generationConfig: GenerationConfig | None = None
    #: `FRD-131`: carried to the model and back, never executed. Whether the use case may use tools
    #: is decided by the layer before dispatch — the shape is valid, the permission is separate.
    tools: list[Tool] = []
    toolConfig: ToolConfig | None = None

    @model_validator(mode="after")
    def _distinct_tool_names(self) -> GenerateContentRequest:
        """Two functions cannot share a name: a call to it could not be routed to one of them, and
        the provider would accept the request and leave the ambiguity to the caller."""
        seen: set[str] = set()
        for tool in self.tools:
            for declaration in tool.functionDeclarations:
                if declaration.name in seen:
                    raise ValueError(
                        f"'{declaration.name}' is declared twice. A call to it could not be "
                        "matched to one function, and the caller would run whichever they found "
                        "first."
                    )
                seen.add(declaration.name)
        return self

    @model_validator(mode="before")
    @classmethod
    def _refuse_unserved(cls, values: Any) -> Any:
        _refuse(values, _REQUEST_NOT_SERVED)
        if isinstance(values, dict) and values.get("thinkingConfig") is not None:
            # Not a Google field at this level, and an easy mistake; `extra="forbid"` would catch
            # it, this says what to do about it.
            raise ValueError(
                "'thinkingConfig' belongs inside 'generationConfig'. At the top level it would "
                "have been ignored, and the request answered with the model's own thinking mode."
            )
        return values


class EmbedContentRequest(BaseModel):
    model_config = _STRICT
    content: Content
    #: Where the `google-genai` SDK puts it: every SDK embedding call goes to
    #: `:batchEmbedContents` with the model inside each entry. Carried and checked, never honoured
    #: as an override — see `names_the_same_model`.
    model: str | None = None
    taskType: str | None = None
    outputDimensionality: int | None = None


class BatchEmbedContentsRequest(BaseModel):
    """Google's batch shape (`FRD-113` §7).

    ``model`` (here and inside each entry) never *selects* anything: the URL named the model, and
    the pre-dispatch controls were applied to that name. A disagreement is refused by name rather
    than answered with a vector from another model (`FRD-124`); a compliant client sends the model
    it addressed.
    """

    model_config = _STRICT
    model: str | None = None
    requests: list[EmbedContentRequest] = Field(min_length=1)


def names_the_same_model(named: str | None, addressed: str) -> bool:
    """Whether a `model` field in an embedding request agrees with the URL.

    Google's clients write the resource form — `models/mock-1` for `mock-1` — so the prefix is
    ignored on both sides. Absent agrees with everything: the field is optional.
    """
    if not named:
        return True
    return named.removeprefix("models/") == addressed.removeprefix("models/")


# -- responses -------------------------------------------------------------------------------------


class Candidate(BaseModel):
    content: Content
    #: Absent until the chunk that ends the message, as Google does it. `""` on intermediate chunks
    #: makes the `google-genai` SDK warn once per chunk.
    finishReason: str | None = None
    index: int


class UsageMetadata(BaseModel):
    promptTokenCount: int
    candidatesTokenCount: int
    totalTokenCount: int
    #: What the model spent thinking, as Google reports it; omitted when zero, as Google does.
    #: **Not gated by `include_reasoning`** (`FRD-135`): that decides whether reasoning *text* is
    #: returned; a token count is about money, and the tokens are billed either way.
    thoughtsTokenCount: int | None = None


class GenerateContentResponse(BaseModel):
    candidates: list[Candidate]
    usageMetadata: UsageMetadata
    modelVersion: str


class GeminiModel(BaseModel):
    name: str
    version: str
    displayName: str
    supportedGenerationMethods: list[str]
    #: The official resource's limits (`FRD-132` §11); a client sizes a conversation against
    #: `inputTokenLimit`. Omitted when unknown: to a client, `0` is not "unknown" but a full window.
    inputTokenLimit: int | None = None
    outputTokenLimit: int | None = None
    # AIRA extensions (`FRD-114` §7): a client can discover what a model may be asked to do — and
    # see when nobody has declared it.
    airaCapabilities: list[str] | None = None
    #: The same figure as `outputTokenLimit`, kept because callers have read it since `FRD-114`.
    airaMaxOutputTokens: int | None = None
    airaDeprecated: bool | None = None
    airaDeclared: bool | None = None
    # Provenance (`FRD-507` FR-1), from the adapter's own configuration, so the console can offer
    # to catalogue what the gateway serves. Capabilities and prices are deliberately not
    # importable: a vendor's flag is a claim (`FRD-131`) and an invented price is worse than none.
    airaProvider: str | None = None
    airaPublisher: str | None = None
    airaRegion: str | None = None


class ListModelsResponse(BaseModel):
    models: list[GeminiModel]


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
