"""Pydantic models for the Gemini (Generative Language API v1beta) wire format.

Field names match Google's camelCase wire shape.

**Requests refuse what they cannot honour (`FRD-124`).** A field silently dropped is the one failure
a gateway built for evidence cannot audit afterwards, and Google's own API rejects unknown fields
too. So: known and portable → carried; known and out of scope → refused by name, with the reason;
unknown → refused, naming the field.

**And they read every spelling Google's own parser reads (`FRD-155`).** The API is protobuf behind
proto3's JSON mapping, so the same request reaches Google written several ways, and clients use
each of them: a field by its camelCase name or by its proto name in snake_case (LiteLLM, and
`google-genai` inside a function declaration), an enum by its name or by its number (every GAPIC
client), a field left at its default — `null`, `[]`, `false`, `0`, `…_UNSPECIFIED` — as a field not
sent (the PHP client sends every field; LangChain always sends `safetySettings: []`), and one object
where a list is expected as a list of one. Each is the same request, so each is read as it; a
refusal still names the field as Google documents it.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_snake

from aira_gateway.core.canonical import MAX_MESSAGES, MAX_TOOLS, too_long_a_conversation

#: Request models forbid what they do not model, and accept each field by its camelCase name and by
#: its proto name (``alias_generator``); a refusal's location names the camelCase field, as
#: Google's reference does (``loc_by_alias``). Response models keep ignoring extras — a provider
#: adding a field must never break a caller, which is the opposite direction and the opposite rule.
_STRICT = ConfigDict(
    extra="forbid", alias_generator=to_snake, populate_by_name=True, loc_by_alias=False
)

#: Google's enum numbers, which a GAPIC client sends in place of the names (proto3's JSON mapping,
#: `google/ai/generativelanguage/v1beta`). ``0`` is each enum's ``…_UNSPECIFIED``: not a value.
FUNCTION_CALLING_MODES = {0: "MODE_UNSPECIFIED", 1: "AUTO", 2: "ANY", 3: "NONE", 4: "VALIDATED"}
MODALITIES = {1: "TEXT", 2: "IMAGE", 3: "AUDIO"}
TASK_TYPES = {
    1: "RETRIEVAL_QUERY",
    2: "RETRIEVAL_DOCUMENT",
    3: "SEMANTIC_SIMILARITY",
    4: "CLASSIFICATION",
    5: "CLUSTERING",
    6: "QUESTION_ANSWERING",
    7: "FACT_VERIFICATION",
    8: "CODE_RETRIEVAL_QUERY",
}
FUNCTION_BEHAVIOURS = {0: "UNSPECIFIED", 1: "BLOCKING", 2: "NON_BLOCKING"}
#: `HarmCategory`. 1–6 are the PaLM models' categories, which no Gemini model filters and Google
#: refuses on a Gemini request; so does this surface, naming the ones it takes.
HARM_CATEGORIES = {
    0: "HARM_CATEGORY_UNSPECIFIED",
    1: "HARM_CATEGORY_DEROGATORY",
    2: "HARM_CATEGORY_TOXICITY",
    3: "HARM_CATEGORY_VIOLENCE",
    4: "HARM_CATEGORY_SEXUAL",
    5: "HARM_CATEGORY_MEDICAL",
    6: "HARM_CATEGORY_DANGEROUS",
    7: "HARM_CATEGORY_HARASSMENT",
    8: "HARM_CATEGORY_HATE_SPEECH",
    9: "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    10: "HARM_CATEGORY_DANGEROUS_CONTENT",
    11: "HARM_CATEGORY_CIVIC_INTEGRITY",
}
#: The categories a Gemini model filters: what Google accepts in a Gemini request's settings.
GEMINI_HARM_CATEGORIES = tuple(HARM_CATEGORIES[number] for number in range(7, 12))
#: `SafetySetting.HarmBlockThreshold`.
HARM_BLOCK_THRESHOLDS = {
    0: "HARM_BLOCK_THRESHOLD_UNSPECIFIED",
    1: "BLOCK_LOW_AND_ABOVE",
    2: "BLOCK_MEDIUM_AND_ABOVE",
    3: "BLOCK_ONLY_HIGH",
    4: "BLOCK_NONE",
    5: "OFF",
}
#: The thresholds that name one: what an entry of the settings must say to ask for anything.
BLOCK_THRESHOLDS = tuple(HARM_BLOCK_THRESHOLDS[number] for number in range(1, 6))


def unset(value: Any) -> bool:
    """Whether ``value`` is proto3's "not set": absent, ``null``, or its type's default.

    Google reads ``[]``, ``""``, ``false``, ``0`` and an enum's ``…_UNSPECIFIED`` as a field that
    was not sent, and so a field refused by design is refused only where it asks for something. An
    object is never "not set", however empty: a message's presence is the request —
    ``"codeExecution": {}`` switches code execution on.
    """
    if value is None or value is False:
        return True
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value == 0
    if isinstance(value, str):
        return not value or value.upper().endswith("_UNSPECIFIED")
    if isinstance(value, list):
        return not value
    return False


def enum_name(value: Any, names: dict[int, str]) -> Any:
    """An enum sent by number, as its name; any other value as it came, for the field to judge."""
    if isinstance(value, int) and not isinstance(value, bool):
        return names.get(value, value)
    return value


def one_is_a_list(value: Any) -> Any:
    """A repeated field sent as one object, or as ``null``: Google reads both, as a list of one and
    as an empty list. A client written against that — the `gemini-ai` Ruby gem sends ``contents``
    and ``parts`` as objects — is sending a valid request."""
    if value is None:
        return []
    return [value] if isinstance(value, dict) else value


def spellings(values: Any, field: str) -> list[Any]:
    """Every value ``values`` holds for ``field``, under either spelling Google reads — before
    anything validated the body, so a bound counted on the raw body cannot be stepped around by
    writing a field the other way."""
    if not isinstance(values, dict):
        return []
    return [values[name] for name in dict.fromkeys((field, to_snake(field))) if name in values]


#: What every provider accepts as a function name, checked here so a caller gets an error naming the
#: field rather than a provider error naming nothing (`FRD-112`'s argument). `\Z`, not `$`: `$` also
#: matches before a trailing newline, and this name reaches the audit row and span attributes.
_FUNCTION_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")

#: A voice is the provider's name for it (`FRD-624`), and a speaker a name the prompt uses. Bounded,
#: because both reach the audit row and an error message.
_VOICE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")
_SPEAKER_NAME = re.compile(r"^[\w .'-]{1,64}\Z")
_LANGUAGE_CODE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}\Z")

#: The roles a turn may carry. `system` and `function` are older spellings Google clients still send
#: (a system turn; a function response). Anything else is refused: an `assistant` turn read as the
#: user's own words changes the conversation, and nothing in the answer would show it.
_ROLES = frozenset({"user", "model", "system", "function"})

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
    "responseLogprobs": "log probabilities are not carried across the four supported dialects",
    "logprobs": "log probabilities are not carried across the four supported dialects",
    "mediaResolution": "media resolution is a Gemini-only control and would not apply uniformly",
    "enableEnhancedCivicAnswers": "a Gemini-only control that would not apply uniformly",
}

#: Top-level fields. `cachedContent` is out of scope by `ADR-0013` (no conversation state).
_REQUEST_NOT_SERVED = {
    "cachedContent": (
        "context caching is not offered (ADR-0013 — no conversation state). Ignoring it would "
        "also mean billing at uncached rates while the caller expected cached ones"
    ),
}


def _listed(found: Any) -> list[Any]:
    """A raw repeated value as the list it will be read as (`one_is_a_list`); anything else is no
    entries, and the validation's to refuse."""
    found = one_is_a_list(found)
    return found if isinstance(found, list) else []


def _entries(raw: Any, field: str) -> int:
    """How many entries ``raw[field]`` holds — before anything validated ``raw``, under both of
    the field's spellings (`spellings`) and with one object counted as the list of one it is."""
    return sum(len(_listed(found)) for found in spellings(raw, field))


def _parts_in(turns: Any) -> int:
    """The parts of every turn in ``turns``, counted on the raw body."""
    return sum(_entries(turn, "parts") for turn in turns)


def _refuse(values: Any, reasons: dict[str, str]) -> Any:
    """Refuse the fields we know about and deliberately do not serve, saying why — under either
    spelling, and only where they ask for something (`unset`).

    Separate from `extra="forbid"`, whose "Extra inputs are not permitted" does not tell a caller
    that a field is refused by design (`ADR-0013`) rather than misspelled.
    """
    if not isinstance(values, dict):
        return values
    for field, reason in reasons.items():
        if any(not unset(value) for value in spellings(values, field)):
            raise ValueError(f"'{field}' is not served by this gateway: {reason}")
    # What is left of them asks for nothing, and is no field of the model either.
    sent = {name for field in reasons for name in (field, to_snake(field))}
    return {key: value for key, value in values.items() if key not in sent}


def settings_that_ask(settings: Any) -> Any:
    """The entries of ``safetySettings`` that name a threshold, under either spelling.

    One without — or with ``HARM_BLOCK_THRESHOLD_UNSPECIFIED`` — leaves its category at the model's
    default, which is what an absent entry does: LangChainGo sends one per category so, where its
    threshold is "unspecified". Left out, it is nothing a dialect without the filter must refuse.
    """
    listed = one_is_a_list(settings)
    if not isinstance(listed, list):
        return listed
    return [
        entry
        for entry in listed
        if not (
            isinstance(entry, dict) and all(unset(found) for found in spellings(entry, "threshold"))
        )
    ]


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


class FunctionResponsePart(BaseModel):
    """Media a function produced — a screenshot, a document it read — as Google carries it."""

    model_config = _STRICT
    inlineData: InlineData


class FunctionResponse(BaseModel):
    """What running it produced, on its way back to the model."""

    model_config = _STRICT
    name: str
    #: An object, as Google defines it; a plain string is refused rather than stringified.
    response: dict[str, Any] = {}
    id: str | None = None
    #: Media beside the object (`FRD-145`): what current clients send when a tool returns an image.
    parts: list[FunctionResponsePart] = []

    _parts_listed = field_validator("parts", mode="before")(one_is_a_list)


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
    #: The opaque signature a thinking model puts beside a function call, and refuses that call
    #: replayed without (`ToolCallPart.signature`). Returned with the call, accepted back on it.
    #: On a text part it is accepted and not forwarded: Google requires it only on a call, and
    #: this gateway returns none on text, so one there was issued to another conversation.
    thoughtSignature: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _refuse_unserved(cls, values: Any) -> Any:
        return _refuse(values, _PART_NOT_SERVED)

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
        if self.thoughtSignature is not None and self.functionCall is None and self.text is None:
            # A function call carries one, and so does text: Gemini 3 signs its first text part,
            # and google-genai replays the turn as it came. Nowhere else does Google put one.
            raise ValueError(
                "'thoughtSignature' is carried on a 'functionCall' or a 'text' part, where the "
                "model that issued it put it"
            )
        return self


class Content(BaseModel):
    model_config = _STRICT
    role: str | None = None
    parts: list[Part]

    _parts_listed = field_validator("parts", mode="before")(one_is_a_list)

    @field_validator("role", mode="before")
    @classmethod
    def _no_role_is_the_default(cls, value: Any) -> Any:
        """An empty role is a role not sent, which Google reads as the user's turn."""
        return None if value == "" else value

    @model_validator(mode="after")
    def _known_role(self) -> Content:
        if self.role is not None and self.role not in _ROLES:
            raise ValueError(
                f"role '{self.role}' is not a Gemini role; a turn is 'user' or 'model'."
            )
        return self


class ThinkingConfig(BaseModel):
    """Google's own field, plus the canonical form (`FRD-111` §7).

    ``thinkingBudget`` is what Google's clients send; ``mode``/``tokens`` is the predecessor's
    vocabulary and the only way to reach the abstract levels. **A request carrying both is a 400**
    rather than a precedence rule nobody can predict from outside.

    The snake_case aliases are what the official `google-genai` SDK puts on the wire inside
    `thinkingConfig` (everything else it sends is camelCase), so refusing them would make the
    common "do not think" configuration unusable from that client.
    """

    model_config = _STRICT
    thinkingBudget: int | None = Field(default=None, alias="thinking_budget")
    #: Google's request to have the reasoning **returned**. Decided per use case at the surface
    #: (`FRD-135` FR-3/FR-4) and refused wherever reasoning is off — a 200 with no thoughts is the
    #: silent drop `FRD-124` is against.
    includeThoughts: bool | None = Field(default=None, alias="include_thoughts")
    #: Gemini 3's level word (`low`, `high`, …), which current SDKs send for it (`FRD-143`). It is
    #: the model's own vocabulary, so it is the canonical `mode` — checked against the levels the
    #: catalog declares for the model, like any level word (`ADR-0021`).
    thinkingLevel: str | None = Field(default=None, alias="thinking_level")
    mode: str | None = None
    tokens: int | None = None

    @model_validator(mode="after")
    def _not_both_spellings(self) -> ThinkingConfig:
        if self.thinkingBudget is not None and (self.mode is not None or self.tokens is not None):
            raise ValueError(
                "send either 'thinkingBudget' or 'mode'/'tokens' in thinkingConfig, not both"
            )
        if self.thinkingLevel is not None and (
            self.thinkingBudget is not None or self.mode is not None or self.tokens is not None
        ):
            raise ValueError(
                "send either 'thinkingLevel' or a budget ('thinkingBudget', 'mode'/'tokens') in "
                "thinkingConfig, not both — Google refuses the pair as well"
            )
        return self


#: Inside `speechConfig` the `google-genai` SDK writes snake_case, as it does inside
#: `thinkingConfig`; Google's documentation writes camelCase. Both are accepted, as everywhere.
_BOTH_SPELLINGS = _STRICT


class PrebuiltVoiceConfig(BaseModel):
    model_config = _BOTH_SPELLINGS
    voiceName: str = Field(alias="voice_name")

    @model_validator(mode="after")
    def _a_voice_name(self) -> PrebuiltVoiceConfig:
        if not _VOICE_NAME.match(self.voiceName):
            raise ValueError(
                f"'{self.voiceName[:64]}' is not a voice name: letters, digits, '_' and '-', up to "
                "64 characters."
            )
        return self


class VoiceConfig(BaseModel):
    model_config = _BOTH_SPELLINGS
    prebuiltVoiceConfig: PrebuiltVoiceConfig = Field(alias="prebuilt_voice_config")


class SpeakerVoiceConfig(BaseModel):
    model_config = _BOTH_SPELLINGS
    speaker: str
    voiceConfig: VoiceConfig = Field(alias="voice_config")

    @model_validator(mode="after")
    def _a_speaker_name(self) -> SpeakerVoiceConfig:
        if not _SPEAKER_NAME.match(self.speaker):
            raise ValueError(
                f"'{self.speaker[:64]}' is not a speaker name: up to 64 letters, digits, spaces "
                "and . ' _ -"
            )
        return self


class MultiSpeakerVoiceConfig(BaseModel):
    model_config = _BOTH_SPELLINGS
    #: Bounded, because each entry reaches the audit row.
    speakerVoiceConfigs: list[SpeakerVoiceConfig] = Field(
        alias="speaker_voice_configs", min_length=1, max_length=8
    )

    @model_validator(mode="after")
    def _distinct_speakers(self) -> MultiSpeakerVoiceConfig:
        names = [entry.speaker for entry in self.speakerVoiceConfigs]
        if len(set(names)) != len(names):
            raise ValueError(
                "A speaker is named twice in 'speakerVoiceConfigs', so their lines could not be "
                "told apart."
            )
        return self


class SpeechConfig(BaseModel):
    """The voice of a spoken answer (`FRD-624`): one voice, or one per named speaker."""

    model_config = _BOTH_SPELLINGS
    voiceConfig: VoiceConfig | None = Field(default=None, alias="voice_config")
    multiSpeakerVoiceConfig: MultiSpeakerVoiceConfig | None = Field(
        default=None, alias="multi_speaker_voice_config"
    )
    languageCode: str | None = Field(default=None, alias="language_code")

    @model_validator(mode="after")
    def _one_way_to_name_a_voice(self) -> SpeechConfig:
        if (self.voiceConfig is None) == (self.multiSpeakerVoiceConfig is None):
            raise ValueError(
                "'speechConfig' names the voice in exactly one of 'voiceConfig' and "
                "'multiSpeakerVoiceConfig'. Without either the provider refuses the request "
                "without saying why; with both it is not clear which applies."
            )
        if self.languageCode is not None and not _LANGUAGE_CODE.match(self.languageCode):
            raise ValueError(
                f"'{self.languageCode[:32]}' is not a language code such as 'de-DE' or 'en-US'."
            )
        return self


class GenerationConfig(BaseModel):
    model_config = _STRICT
    temperature: float | None = None
    maxOutputTokens: int | None = None
    thinkingConfig: ThinkingConfig | None = None
    responseMimeType: str | None = None
    responseSchema: dict[str, Any] | None = None
    #: The same in JSON Schema (`FRD-143`), which current SDKs send for structured output. One or
    #: the other, as at Google.
    responseJsonSchema: dict[str, Any] | None = None
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
    #: `["AUDIO"]` asks for the answer as speech (`FRD-624`); `["TEXT"]` is what happens without it.
    responseModalities: list[str] | None = None
    speechConfig: SpeechConfig | None = None

    @property
    def speaks(self) -> bool:
        """Whether the answer is asked for as speech (`FRD-624`)."""
        return [value.upper() for value in self.responseModalities or []] == ["AUDIO"]

    @model_validator(mode="before")
    @classmethod
    def _refuse_unserved(cls, values: Any) -> Any:
        return _refuse(values, _CONFIG_NOT_SERVED)

    @field_validator("candidateCount", "responseMimeType", mode="before")
    @classmethod
    def _default_is_not_set(cls, value: Any) -> Any:
        """``0`` candidates and an empty media type are proto3's defaults: a field not sent, which
        Google answers with one candidate in text."""
        return None if unset(value) else value

    @field_validator("responseModalities", mode="before")
    @classmethod
    def _modalities_by_name(cls, value: Any) -> Any:
        """Modalities by number (`MODALITIES`) as by name; an unspecified one names none."""
        if not isinstance(value, list):
            return value
        named = [enum_name(entry, MODALITIES) for entry in value]
        return [entry for entry in named if not unset(entry)] or None

    @model_validator(mode="after")
    def _one_candidate(self) -> GenerationConfig:
        if self.candidateCount is not None and self.candidateCount != 1:
            raise ValueError(
                f"'candidateCount' must be 1; this gateway returns one candidate, and answering a "
                f"request for {self.candidateCount} with one would look like a complete answer."
            )
        return self

    @model_validator(mode="after")
    def _speech_alone(self) -> GenerationConfig:
        """Speech alone, or text (`FRD-624`). Anything else the provider refuses without a reason,
        or answers in prose where images were asked for."""
        modalities = [value.upper() for value in self.responseModalities or []]
        if modalities not in ([], ["TEXT"], ["AUDIO"]):
            raise ValueError(
                f"'responseModalities' {self.responseModalities} is not served: this gateway "
                "answers in text, or with speech alone (['AUDIO']). A speech model returns audio "
                "only, and a request for images would be answered in prose."
            )
        if self.speaks and self.speechConfig is None:
            raise ValueError(
                "'responseModalities' ['AUDIO'] needs a 'speechConfig' naming the voice. Without "
                "one the provider refuses the request without saying why."
            )
        if self.speechConfig is not None and not self.speaks:
            raise ValueError(
                "'speechConfig' sets the voice of a spoken answer; send it with "
                "'responseModalities': ['AUDIO']."
            )
        if self.speaks and (self.responseSchema is not None or self.responseJsonSchema is not None):
            raise ValueError("A spoken answer cannot conform to a 'responseSchema'.")
        if self.speaks and self.thinkingConfig is not None and self.thinkingConfig.includeThoughts:
            raise ValueError(
                "'includeThoughts' asks for reasoning, and a speech model returns audio only."
            )
        return self

    @model_validator(mode="after")
    def _served_media_type(self) -> GenerationConfig:
        """Text, or JSON **with** a schema (`FRD-112`): any other type would be answered in prose,
        and JSON without a schema has no counterpart on every supported dialect."""
        mime = self.responseMimeType
        if self.responseSchema is not None and self.responseJsonSchema is not None:
            raise ValueError(
                "'responseSchema' and 'responseJsonSchema' are both set. Send one: it is not clear "
                "which the answer should match."
            )
        schema = self.responseSchema if self.responseSchema is not None else self.responseJsonSchema
        if self.responseJsonSchema is not None and mime != "application/json":
            raise ValueError(
                "'responseJsonSchema' needs 'responseMimeType' application/json, as Google's API "
                "requires; without it the answer would arrive as prose."
            )
        if mime is None or mime == "text/plain":
            return self
        if mime == "application/json" and schema is not None:
            return self
        if mime == "application/json":
            raise ValueError(
                "'responseMimeType' application/json without a 'responseSchema' is not served: "
                "JSON without a schema is not expressed on every supported dialect, so the answer "
                "would arrive as prose. Send a responseSchema, or omit responseMimeType."
            )
        raise ValueError(
            f"'responseMimeType' '{mime}' is not served: this gateway answers in text/plain, or in "
            "application/json with a 'responseSchema'."
        )


#: Google's function-calling modes, and those that make a call `allowedFunctionNames` can narrow.
_TOOL_MODES = frozenset({"AUTO", "ANY", "NONE", "VALIDATED"})
_MODES_THAT_CALL = frozenset({"ANY", "VALIDATED"})


class FunctionDeclaration(BaseModel):
    """A function the caller offers the model (`FRD-131`).

    ``parameters`` stays raw JSON here and is parsed by `core.schema` in the mapper — the same
    parser, bounds and error vocabulary a `responseSchema` gets. ``parametersJsonSchema`` is the
    same declaration in JSON Schema, which current SDKs send (`FRD-143`): bounded alike and carried
    as written. Google takes one or the other, never both, and so does this surface.
    """

    model_config = _BOTH_SPELLINGS
    name: str
    description: str = ""
    parameters: dict[str, Any] | None = None
    parametersJsonSchema: dict[str, Any] | None = Field(
        default=None, alias="parameters_json_schema"
    )
    #: What the function returns, in either vocabulary (`FRD-155`): Google's own fields, which the
    #: `google-genai` SDKs fill from a typed function's return annotation whenever they run its
    #: calls themselves. Bounded like the parameters and carried to a Gemini model; a dialect that
    #: has no word for it is passed over by name rather than sent the declaration without it.
    response: dict[str, Any] | None = None
    responseJsonSchema: dict[str, Any] | None = None
    #: Whether the model waits for the result. Only ``BLOCKING`` exists in a request and its answer.
    behavior: str | None = None

    @field_validator("behavior", mode="before")
    @classmethod
    def _behaviour_by_name(cls, value: Any) -> Any:
        named = enum_name(value, FUNCTION_BEHAVIOURS)
        return None if unset(named) else named

    @model_validator(mode="after")
    def _one_way_to_declare_parameters(self) -> FunctionDeclaration:
        if self.parameters is not None and self.parametersJsonSchema is not None:
            raise ValueError(
                f"'{self.name}' declares its parameters twice, in 'parameters' and in "
                "'parametersJsonSchema'. Send one: it is not clear which the model should follow."
            )
        if self.response is not None and self.responseJsonSchema is not None:
            raise ValueError(
                f"'{self.name}' declares what it returns twice, in 'response' and in "
                "'responseJsonSchema'. Send one: it is not clear which the model should expect."
            )
        return self

    @model_validator(mode="after")
    def _a_result_in_the_same_call(self) -> FunctionDeclaration:
        if self.behavior not in (None, "BLOCKING"):
            raise ValueError(
                f"'behavior' '{self.behavior}' is not served: the model goes on while the function "
                "runs only in Google's Live API. A request here waits for the result it is sent, "
                "so the declaration is 'BLOCKING' or says nothing."
            )
        return self

    @model_validator(mode="after")
    def _usable_name(self) -> FunctionDeclaration:
        if not _FUNCTION_NAME.match(self.name):
            raise ValueError(
                f"'{self.name}' is not a usable function name. Providers accept letters, digits, "
                "'_' and '-', up to 64 characters — a name outside that is rejected downstream "
                "with a message that names neither the tool nor the field."
            )
        return self


#: Google's own tools, which run at Google: refused by name (`ADR-0013` — this gateway carries the
#: caller's own functions and executes nothing), and an empty object is no exception, since its
#: presence is what switches the tool on.
_GOOGLE_TOOL = (
    "a tool run at Google rather than by the caller; this gateway carries the caller's own "
    "functions and runs nothing itself (ADR-0013), and no other dialect has it"
)
_TOOL_NOT_SERVED = {
    "googleSearch": _GOOGLE_TOOL,
    "googleSearchRetrieval": _GOOGLE_TOOL,
    "codeExecution": "code execution is not offered (ADR-0013)",
    "urlContext": _GOOGLE_TOOL,
    "computerUse": _GOOGLE_TOOL,
    "fileSearch": _GOOGLE_TOOL,
    "googleMaps": _GOOGLE_TOOL,
}


class Tool(BaseModel):
    """Google nests declarations one level down, in a list of tools."""

    model_config = _STRICT
    functionDeclarations: list[FunctionDeclaration] = []

    _declarations_listed = field_validator("functionDeclarations", mode="before")(one_is_a_list)

    @model_validator(mode="before")
    @classmethod
    def _refuse_unserved(cls, values: Any) -> Any:
        return _refuse(values, _TOOL_NOT_SERVED)


class FunctionCallingConfig(BaseModel):
    """How the model is steered toward calling a function: Google's four modes.

    `AUTO` is the default and what most clients send. `ANY` requires a call — OpenCode asks for one
    when it wants a structured answer through a tool —, `NONE` forbids one — sent on the last step
    of a bounded agent run —, and `VALIDATED` is `AUTO` with the arguments guaranteed to match.
    `allowedFunctionNames` narrows which functions a call may name, with `ANY` and `VALIDATED`.
    Each provider spells them differently, and a provider that cannot say one is skipped by name.
    """

    model_config = _BOTH_SPELLINGS
    mode: str = "AUTO"
    allowedFunctionNames: list[str] | None = Field(default=None, alias="allowed_function_names")

    @field_validator("mode", mode="before")
    @classmethod
    def _mode_by_name(cls, value: Any) -> Any:
        """A mode by number (`FUNCTION_CALLING_MODES`) as by name; none sent is ``AUTO``."""
        named = enum_name(value, FUNCTION_CALLING_MODES)
        return "AUTO" if unset(named) else named

    @model_validator(mode="after")
    def _a_mode_google_has(self) -> FunctionCallingConfig:
        mode = self.mode.strip().upper()
        if mode == "MODE_UNSPECIFIED":
            mode = "AUTO"
        if mode not in _TOOL_MODES:
            raise ValueError(
                f"'{self.mode}' is not a function-calling mode. Google's are "
                f"{', '.join(sorted(_TOOL_MODES))}."
            )
        self.mode = mode
        if self.allowedFunctionNames and mode not in _MODES_THAT_CALL:
            raise ValueError(
                f"'allowedFunctionNames' narrows which function a call may name, and mode "
                f"'{mode}' makes no call it could narrow. Send it with 'ANY' or 'VALIDATED'."
            )
        return self


class ToolConfig(BaseModel):
    model_config = _STRICT
    functionCallingConfig: FunctionCallingConfig | None = None


class SafetySetting(BaseModel):
    """One harm filter (`FRD-155`): a category a Gemini model filters, and a threshold Google names.

    An entry naming no threshold asks for nothing and never reaches this model
    (`settings_that_ask`). A name is read in either case, and a number as its name.
    """

    model_config = _STRICT
    category: str
    threshold: str

    @field_validator("category", mode="before")
    @classmethod
    def _a_category_gemini_filters(cls, value: Any) -> Any:
        named = enum_name(value, HARM_CATEGORIES)
        if isinstance(named, str) and named.strip().upper() in GEMINI_HARM_CATEGORIES:
            return named.strip().upper()
        raise ValueError(
            f"'{named}' is not a harm category a Gemini model filters. Google's are "
            f"{', '.join(GEMINI_HARM_CATEGORIES)}."
        )

    @field_validator("threshold", mode="before")
    @classmethod
    def _a_threshold_google_names(cls, value: Any) -> Any:
        named = enum_name(value, HARM_BLOCK_THRESHOLDS)
        if isinstance(named, str) and named.strip().upper() in BLOCK_THRESHOLDS:
            return named.strip().upper()
        raise ValueError(
            f"'{named}' is not a block threshold. Google's are {', '.join(BLOCK_THRESHOLDS)}."
        )


class GenerateContentRequest(BaseModel):
    model_config = _STRICT
    #: The model, where a client repeats the one its URL names: Google's request message has the
    #: field, and the GAPIC clients (the Go SDK, LangChainGo) send it in the body as well. It never
    #: selects anything — the URL does, as for an embedding (`names_the_same_model`).
    model: str | None = None
    contents: list[Content] = Field(min_length=1)
    systemInstruction: Content | None = None
    generationConfig: GenerationConfig | None = None
    #: `FRD-131`: carried to the model and back, never executed. Whether the use case may use tools
    #: is decided by the layer before dispatch — the shape is valid, the permission is separate.
    tools: list[Tool] = []
    toolConfig: ToolConfig | None = None
    #: The caller's harm filters (`FRD-155`): carried to a Gemini model, and a candidate whose
    #: dialect has no such filter is passed over by name.
    safetySettings: list[SafetySetting] = []

    #: A single tool object is a list of one (`FRD-143`): the AI SDK's Google provider sent the
    #: object in its first major, which older agents still run. A single turn is one too.
    _listed = field_validator("contents", "tools", mode="before")(one_is_a_list)
    _asking = field_validator("safetySettings", mode="before")(settings_that_ask)

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

    @model_validator(mode="after")
    def _steers_toward_declared_functions(self) -> GenerateContentRequest:
        """A mode that requires a call, or names the functions it may be, needs those functions
        declared: otherwise the model is told to call something it was never offered."""
        calling = self.toolConfig.functionCallingConfig if self.toolConfig else None
        if calling is None:
            return self
        declared = {d.name for tool in self.tools for d in tool.functionDeclarations}
        if calling.mode == "ANY" and not declared:
            raise ValueError(
                "'functionCallingConfig' mode 'ANY' requires a call, and no function is declared."
            )
        unknown = sorted(set(calling.allowedFunctionNames or ()) - declared)
        if unknown:
            raise ValueError(
                f"'allowedFunctionNames' names {', '.join(unknown)}, which this request does not "
                "declare."
            )
        return self

    @model_validator(mode="after")
    def _speech_reads_only_the_text(self) -> GenerateContentRequest:
        """A speech model refuses a `systemInstruction` without saying why (`FRD-624`)."""
        speaks = self.generationConfig is not None and self.generationConfig.speaks
        if speaks and self.systemInstruction is not None:
            raise ValueError(
                "A speech model reads only the text it is to speak and refuses a "
                "'systemInstruction'. Put the style in the text, such as 'Say cheerfully: …'."
            )
        return self

    @model_validator(mode="before")
    @classmethod
    def _counted_before_validated(cls, values: Any) -> Any:
        """The turns, their parts and the functions, counted on the raw body before any of them is
        validated: past the bounds (`core.canonical`), validating them is the cost the bound is
        there to refuse, and a count afterwards would only report it."""
        if not isinstance(values, dict):
            return values
        # Under both spellings and with one object read as a list of one, as validation reads
        # them: a count that missed a spelling would be a way past the bound.
        turns = [turn for found in spellings(values, "contents") for turn in _listed(found)]
        systems = [found for found in spellings(values, "systemInstruction") if found is not None]
        messages = len(turns) + len(systems)
        # Not counted past the turns' own bound: the refusal is decided, and the count would walk
        # the very list it refuses.
        parts = 0 if messages > MAX_MESSAGES else _parts_in((*turns, *systems))
        problem = too_long_a_conversation(messages, parts)
        if problem is not None:
            raise ValueError(problem)
        tools = [tool for found in spellings(values, "tools") for tool in _listed(found)]
        declared = sum(_entries(tool, "functionDeclarations") for tool in tools)
        if declared > MAX_TOOLS:
            raise ValueError(
                f"'tools' declares {declared} functions, more than the {MAX_TOOLS} one request may "
                "offer — Google's own maximum for a request to Gemini."
            )
        return values

    @model_validator(mode="before")
    @classmethod
    def _refuse_unserved(cls, values: Any) -> Any:
        values = _refuse(values, _REQUEST_NOT_SERVED)
        if any(found is not None for found in spellings(values, "thinkingConfig")):
            # Not a Google field at this level, and an easy mistake; `extra="forbid"` would catch
            # it, this says what to do about it.
            raise ValueError(
                "'thinkingConfig' belongs inside 'generationConfig'. At the top level it would "
                "have been ignored, and the request answered with the model's own thinking mode."
            )
        return values


class CountTokensRequest(BaseModel):
    """What to count (`FRD-155`): a conversation, or a whole generation request — system
    instruction and declared functions included, which every SDK's deprecated generation sends.

    One or the other, as Google's reference says; both is refused rather than one of them dropped.
    """

    model_config = _STRICT
    #: As for a generation: repeated from the URL by a GAPIC client, checked and never honoured.
    model: str | None = None
    contents: list[Content] = []
    generateContentRequest: GenerateContentRequest | None = None

    _listed = field_validator("contents", mode="before")(one_is_a_list)

    @model_validator(mode="after")
    def _one_request(self) -> CountTokensRequest:
        if self.generateContentRequest is not None and self.contents:
            raise ValueError(
                "'contents' and 'generateContentRequest' each say what to count; send one of "
                "them. Counting one and ignoring the other would answer a question nobody asked."
            )
        if self.generateContentRequest is None and not self.contents:
            raise ValueError("Nothing to count: send 'contents' or a 'generateContentRequest'.")
        return self

    def generation(self) -> GenerateContentRequest:
        """The request counted, as the generation it stands for."""
        if self.generateContentRequest is not None:
            return self.generateContentRequest
        return GenerateContentRequest(contents=self.contents)


class CountTokensResponse(BaseModel):
    #: The vendor's own count of what the request is billed for as input.
    totalTokens: int


class EmbedContentRequest(BaseModel):
    model_config = _STRICT
    content: Content
    #: Where the `google-genai` SDK puts it: every SDK embedding call goes to
    #: `:batchEmbedContents` with the model inside each entry. Carried and checked, never honoured
    #: as an override — see `names_the_same_model`.
    model: str | None = None
    taskType: str | None = None
    #: The document's title (`FRD-155`), which Google reads with ``RETRIEVAL_DOCUMENT`` for better
    #: retrieval. Carried where the model's dialect has the field, through the use case's pipeline
    #: like the text it titles; a dialect without it is passed over by name.
    title: str | None = None
    outputDimensionality: int | None = None

    @field_validator("taskType", mode="before")
    @classmethod
    def _task_by_name(cls, value: Any) -> Any:
        """A task type by number (`TASK_TYPES`) as by name; an unspecified one names none."""
        named = enum_name(value, TASK_TYPES)
        return None if unset(named) else named


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

    _requests_listed = field_validator("requests", mode="before")(one_is_a_list)


def names_the_same_model(named: str | None, addressed: str) -> bool:
    """Whether a `model` field in a request body agrees with the URL.

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
    #: Of the prompt, what a provider's cache served (`FRD-133`) — what a client reads to show the
    #: cache working and to price the turn. Omitted when zero, as Google does.
    cachedContentTokenCount: int | None = None


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
    #: Google's own place for machine-readable detail: a list of typed messages, here one
    #: `google.rpc.ErrorInfo` naming which control refused (`FRD-628` FR-3). Left out when there is
    #: none, as Google leaves it out.
    details: list[dict[str, Any]] | None = None


class GeminiError(BaseModel):
    error: GeminiErrorDetail
