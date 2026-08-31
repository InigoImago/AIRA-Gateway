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

import re
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


#: Part shapes Google defines that AIRA does not serve.
#:
#: `functionCall` and `functionResponse` **left this list on 2026-08-08** (`FRD-131`): they are
#: carried now. The distinction `ADR-0013` always drew is between passing a declaration through
#: and *executing* something, and only the second is out of scope. `executableCode` is the first —
#: it asks a provider to run code on our behalf, which is the thing that stays refused.
_PART_NOT_SERVED = {
    "executableCode": "code execution is not offered (ADR-0013)",
    "codeExecutionResult": "code execution is not offered (ADR-0013)",
    "fileData": (
        "attachments are sent inline as 'inlineData'; a file reference would be dropped and the "
        "model would answer about a document it never received (FRD-110)"
    ),
}


class FunctionCall(BaseModel):
    """The model asking for a function to be run — by the caller (`FRD-131`)."""

    model_config = _STRICT
    name: str
    args: dict[str, Any] = {}
    #: Google matches a result to a call by **name**; the other two dialects require an id. One is
    #: accepted if a client sends it and generated otherwise, so a conversation that starts here
    #: can be continued anywhere.
    id: str | None = None


class FunctionResponse(BaseModel):
    """What running it produced, on its way back to the model."""

    model_config = _STRICT
    name: str
    #: Google's shape is an object; a caller who has a plain string gets an error naming the field
    #: rather than a silent stringification.
    response: dict[str, Any] = {}
    id: str | None = None


class Part(BaseModel):
    """One part of a prompt: text, inline data, a function call or a function response — **exactly
    one of them**.

    Modelled as optional fields with a validator rather than a union, because Google's wire format
    is a single object shape and a caller who sends `{}` deserves an error naming the problem, not
    a union-discrimination message listing four schemas.
    """

    model_config = _STRICT
    text: str | None = None
    inlineData: InlineData | None = None
    functionCall: FunctionCall | None = None
    functionResponse: FunctionResponse | None = None
    #: Google's marker for a reasoning part (`FRD-135`). Set on the way **out**, where a use case
    #: has enabled reasoning; a caller who sends it on the way in is sending a field about the
    #: model's own output, which `_refuse_unserved` handles like any other unserved field.
    #:
    #: It is the whole reason reasoning can be returned at all: without it the thoughts arrive in
    #: the same `parts` array as the answer and nothing distinguishes them.
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

    ``thinkingBudget`` is what Google's clients already send. ``mode``/``tokens`` is the vocabulary
    the predecessor's clients use and the only way to reach the abstract levels, whose budgets are
    per model — so both spellings are accepted and **a request carrying both is a 400**, rather
    than a silent precedence rule nobody can predict from the outside.

    **And `thinking_budget`, in snake_case, because that is what the SDK actually sends.**
    Measured on 2026-08-12 against the running gateway: every field the `google-genai` client
    serialises is camelCase — `maxOutputTokens`, `topP`, `stopSequences`, `responseMimeType`,
    `systemInstruction` — *except* the two inside `thinkingConfig`, which come out as
    `thinking_budget` and `include_thoughts`. An inconsistency in the client, and irrelevant which
    of us thinks it is wrong: it is what the official client puts on the wire, so a surface that
    refuses it is not Gemini-compatible.

    The consequence was the worst available. "Do not think" is the single most common
    configuration for a governed gateway — it is what this project's own demo traffic sets on every
    request — and from the official SDK it answered `400 Extra inputs are not permitted`. Nothing
    could be done about it from the client side, since the client chooses the spelling.

    Third field this week that a real SDK sends in a place we did not anticipate, after the
    embedding `model` and the empty `finishReason`. All three were invisible to every test here,
    for one reason: our tests send what we believe the SDK sends.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    thinkingBudget: int | None = Field(default=None, alias="thinking_budget")
    #: Google's request to have the model's reasoning **returned**. `false` asks for exactly what
    #: this gateway does, so it is carried and means nothing; `true` is refused by name, because
    #: thinking blocks are dropped and never persisted (`FRD-119` §5.4) and answering a request for
    #: them with a 200 and no thoughts is the silent-drop failure `FRD-124` is about.
    includeThoughts: bool | None = Field(default=None, alias="include_thoughts")
    mode: str | None = None
    tokens: int | None = None

    @model_validator(mode="after")
    def _not_both_spellings(self) -> ThinkingConfig:
        if self.thinkingBudget is not None and (self.mode is not None or self.tokens is not None):
            raise ValueError(
                "send either 'thinkingBudget' or 'mode'/'tokens' in thinkingConfig, not both"
            )
        # `includeThoughts: true` used to be refused outright. It is decided per **use case** now
        # (`FRD-135` FR-3/FR-4), and the check moved to where the use case is known — the surface,
        # not the schema. The refusal itself is unchanged wherever reasoning is off: answering 200
        # with no thoughts is the silent drop `FRD-124` exists against.
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


#: What every provider accepts as a function name. Checked **here**, at our boundary, so a caller
#: gets an error naming the field instead of a provider error naming nothing — the same argument
#: `FRD-112` makes for parsing a schema rather than forwarding it blindly.
#: `\Z`, for the reason `auth/attribution._SLUG` states: `$` also matches before a trailing
#: newline, and this name is written into the audit row and the span attributes of every tool call.
_FUNCTION_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")


class FunctionDeclaration(BaseModel):
    """A function the caller offers the model (`FRD-131`).

    ``parameters`` is left as raw JSON here and parsed by `core.schema` in the mapper — the same
    parser, bounds and error vocabulary a `responseSchema` gets, because it is the same kind of
    thing arriving through a different field.
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
    """How the model is steered toward calling a function.

    **`AUTO` only, and the reason is a measurement.** This whole object was refused outright at
    first, on the argument that its modes hold on one vendor and silently do not on another. Then a
    real client was pointed at the gateway and sent `AUTO` on **every** request — because `AUTO`
    *is* the default: it asks for exactly what happens when nothing is sent at all. Refusing it
    blocked the entire use case in the name of a fidelity problem it does not have.

    `ANY` (the model must call something) and `NONE` (it must not) are different: they change the
    answer, each dialect spells them differently, and the mapping is **not built**. They are
    therefore refused **by name and with that reason** — not with a claim about vendors, which is
    what the first version did without having measured one.
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
    #: `FRD-131`. Carried to the model and back; never executed. A use case that has not enabled
    #: tool calling is refused before dispatch, by the layer rather than by this schema — the
    #: shape is valid, the *permission* is what is missing, and the two deserve different messages.
    tools: list[Tool] = []
    #: Accepted for `AUTO` — which is what the model does anyway — and refused by name for the
    #: modes that would change the answer and are not implemented.
    toolConfig: ToolConfig | None = None

    @model_validator(mode="after")
    def _distinct_tool_names(self) -> GenerateContentRequest:
        """Two functions cannot share a name.

        A model asked for `read` when two `read`s were declared has given an answer nobody can
        route — and the caller would execute *one of them*, chosen by whichever their code found
        first. Refused here rather than at the provider, which accepts the request and leaves the
        ambiguity to be discovered by a wrong file being read.
        """
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
    #: **Absent while the answer is still coming.** Google sets this on the chunk that ends the
    #: message and omits it on the others; we sent `""` on every intermediate chunk, and the
    #: `google-genai` SDK answers that with `UserWarning: '' is not a valid FinishReason` — once
    #: per chunk, so a hundred lines of warning for one ordinary streamed answer. Nothing breaks,
    #: which is why no test here noticed: our own client is a dict, and a dict does not have
    #: opinions about an enum. Found by running the real SDK against the app (2026-08-12).
    finishReason: str | None = None
    index: int


class UsageMetadata(BaseModel):
    promptTokenCount: int
    candidatesTokenCount: int
    totalTokenCount: int
    #: What the model spent thinking, which Google's own `usageMetadata` reports and this surface
    #: did not. The gateway had the figure all along — it reads `thoughtsTokenCount` from the
    #: upstream and records it on the audit row as `reasoning_tokens` — and simply never handed it
    #: back, so a caller could see that a request cost 796 completion tokens and not that 764 of
    #: them were thinking. That is most of the bill, invisible, on the one number a caller checks.
    #:
    #: Omitted when zero rather than sent as `0`, because Google omits it for a model that did not
    #: think and a compatibility surface should not invent a field the original leaves out.
    #:
    #: **Not gated by `include_reasoning`** (`FRD-135`): that decides whether the reasoning *text*
    #: comes back and is stored, which is a question about content. A token count is a question
    #: about money, and the tokens are billed either way.
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
    #: **The two limits the official resource carries** (`FRD-132` §11), and this surface did not.
    #:
    #: Google publishes `inputTokenLimit` and `outputTokenLimit` on every model, and a client sizes
    #: a conversation against the first: a coding assistant's *"12% of the context used"* is that
    #: number underneath. AIRA had the second all along and published it as `airaMaxOutputTokens` —
    #: an invented name beside a standard one, which is the one thing a compatibility surface must
    #: not do, because no client written against Google reads it.
    #:
    #: Measured before this existed: OpenCode, pointed at AIRA, resolved
    #: `limit: {context: 0, output: 0}` and showed a context gauge stuck at 0%.
    #:
    #: Omitted when unknown rather than sent as `0`. Google omits them for a model it has no figure
    #: for, and a zero here is not "unknown" to a client — it is a full context window.
    inputTokenLimit: int | None = None
    outputTokenLimit: int | None = None
    # AIRA extensions (FRD-114 §7): a client can discover what a model may be asked to do rather
    # than reading our documentation — and, more usefully, see when nobody has declared it.
    airaCapabilities: list[str] | None = None
    #: Kept beside `outputTokenLimit`, which now carries the same figure. Removing it would break
    #: any caller that has been reading it since `FRD-114`, and a compatibility surface does not
    #: get to withdraw a field to tidy up. New callers should read the standard one.
    airaMaxOutputTokens: int | None = None
    airaDeprecated: bool | None = None
    airaDeclared: bool | None = None
    # Provenance (`FRD-507` FR-1). Where the model lives is a fact the adapter has — it built this
    # entry from its own configuration, and these three already reach every audit row from there.
    # They are here so the console can offer to catalogue what the gateway serves without anybody
    # retyping it. Capabilities and prices are deliberately *not* importable: a vendor's flag is a
    # claim (`FRD-131`) and an invented price is worse than an absent one (`FRD-403`).
    airaProvider: str | None = None
    airaPublisher: str | None = None
    airaRegion: str | None = None


class ListModelsResponse(BaseModel):
    models: list[GeminiModel]


class EmbedContentRequest(BaseModel):
    model_config = _STRICT
    content: Content
    #: **Where the `google-genai` SDK actually puts it.** It was declared one level up, on the
    #: batch wrapper, by somebody who anticipated the field and guessed the level — so a plain
    #: `client.models.embed_content(...)` was refused with `requests.0.model: Extra inputs are not
    #: permitted`. Every embedding call the SDK makes goes to `:batchEmbedContents` with the model
    #: inside each entry, single text or not, so this was the whole verb being unusable from the
    #: official client. Found by running that client against this app; no test written here could
    #: have found it, because they all send what we believe the SDK sends.
    #:
    #: Carried, checked, never honoured as an override — see `names_the_same_model`.
    model: str | None = None
    taskType: str | None = None
    outputDimensionality: int | None = None


class BatchEmbedContentsRequest(BaseModel):
    """Google's batch shape (`FRD-113` §7).

    ``model`` is accepted here and inside each entry, and in neither place does it *select*
    anything: the URL named the model, and the pre-dispatch controls have already been applied to
    that name. Honouring an override would let one request address a model nothing checked.

    But it is not ignored either. A caller who names a different model there is asking for
    something this surface will not do, and answering with a confident vector from another model
    is `FRD-124`'s defect exactly — a field accepted, dropped, and a 200. So a disagreement is
    refused by name. A compliant client never sees it: the SDK sends the model it addressed.
    """

    model_config = _STRICT
    model: str | None = None
    requests: list[EmbedContentRequest] = Field(min_length=1)


def names_the_same_model(named: str | None, addressed: str) -> bool:
    """Whether a `model` field in an embedding request agrees with the URL.

    Google's clients write the **resource form** — `models/mock-1` for `mock-1` — which is the
    same trap `FRD-507` hit when an import would have catalogued `models/…` as a model name.
    Absent agrees with everything: the field is optional.
    """
    if not named:
        return True
    return named.removeprefix("models/") == addressed.removeprefix("models/")


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
