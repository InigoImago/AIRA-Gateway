"""No request field is accepted and thrown away (FRD-124).

This suite exists because of a live run, not a code review. Twelve fields a Google client can
legitimately send were posted at a working gateway; **eleven came back 200 and had no effect**. A
caller who set `stopSequences` got unbounded output, one who set a `seed` for reproducibility got a
different answer every time, one who sent `tools` got prose, and one who sent `safetySettings` got
a governance control that was never applied anywhere. Nothing in any response said so.

The project already refuses to degrade a request silently when the *model* cannot do something —
attachments, schemas, thinking, region. This is the same rule pointed at the *surface*: what a
gateway accepts is a promise, and accepting a field is a promise to honour it.

Three shapes of answer:

    portable and supported     → carried to the dialect       (`topP`, `seed`, `stopSequences`, …)
    known but out of scope     → refused, saying why          (`toolConfig`, `safetySettings`, …)
    the dialect cannot say it  → the candidate is skipped     (`top_k` on OpenAI, `seed` on Claude)

**`tools` moved from the second row to the first on 2026-08-08** (`FRD-131`). It was refused
because the canonical core had nowhere to put a declaration, and the refusal cited `ADR-0013` —
which forbids *executing* a tool and has always allowed carrying one through. The refusal was the
right answer to a missing capability and the wrong answer once the capability exists. Nothing about
the rule changed; one field changed rows, and the cases below moved with it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aira_gateway.api.gemini import schemas
from aira_gateway.api.gemini.mapping import gemini_to_canonical
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import SAMPLING_CONTROLS, CanonicalMessage, CanonicalRequest, Role
from aira_gateway.core.schema import SchemaBounds
from aira_gateway.requirements import SamplingExpressible
from aira_gateway.upstreams.base import DialectUnsupported, ProviderRegistry
from aira_gateway.upstreams.gemini_mapping import SAMPLING as GEMINI_SAMPLING
from aira_gateway.upstreams.gemini_mapping import canonical_to_gemini_request
from aira_gateway.upstreams.openai.mapping import SAMPLING as OPENAI_SAMPLING
from aira_gateway.upstreams.openai.mapping import canonical_to_openai
from aira_gateway.upstreams.vertex.anthropic_mapping import SAMPLING as ANTHROPIC_SAMPLING
from aira_gateway.upstreams.vertex.anthropic_mapping import canonical_to_anthropic


def _client() -> TestClient:
    return TestClient(create_app(GatewaySettings(auth_required=False, log_queue_size=0)))


def _post(body: dict) -> tuple[int, str]:
    with _client() as client:
        response = client.post("/v1beta/models/mock-1:generateContent", json=body)
    message = ""
    if response.status_code != 200:
        message = response.json()["error"]["message"]
    return response.status_code, message


def _base(**config: object) -> dict:
    return {
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
        **({"generationConfig": config} if config else {}),
    }


# == known, out of scope: refused by name, with the reason ========================================


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        # `AUTO` is carried — it is the default, and a real client sends it on every request.
        # `ANY` changes the answer, is spelled differently by each dialect, and is not built.
        ("toolConfig", {"functionCallingConfig": {"mode": "ANY"}}, "not served"),
        ("cachedContent", "cachedContents/abc", "context caching"),
        ("safetySettings", [{"category": "HARM_CATEGORY_HARASSMENT"}], "safety"),
    ],
)
def test_a_field_this_gateway_does_not_serve_is_refused_and_says_why(
    field: str, value: object, expected: str
) -> None:
    """All of these were served with a 200 and dropped.

    `toolConfig` stays refused now that `tools` is carried, and for a reason of its own: its modes
    hold on one vendor and silently do not on another, so a caller who forced a function call would
    sometimes get a suggestion instead — with nothing in the response to say which they got.
    """
    status, message = _post({**_base(), field: value})
    assert status == 400
    assert field in message
    assert expected in message


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("responseModalities", ["AUDIO"]),
        ("speechConfig", {"voiceConfig": {}}),
        ("responseLogprobs", True),
        ("logprobs", 5),
        ("mediaResolution", "MEDIA_RESOLUTION_LOW"),
        ("enableEnhancedCivicAnswers", True),
    ],
)
def test_a_generation_config_field_this_gateway_does_not_serve_is_refused(
    field: str, value: object
) -> None:
    status, message = _post(_base(**{field: value}))
    assert status == 400
    assert field in message


@pytest.mark.parametrize(
    "part",
    [
        {"executableCode": {"language": "PYTHON", "code": "1"}},
        {"fileData": {"mimeType": "application/pdf", "fileUri": "gs://b/o"}},
    ],
)
def test_a_part_shape_this_gateway_does_not_serve_is_refused(part: dict) -> None:
    """`functionCall` and `functionResponse` left this list with `FRD-131` — they are carried, and
    `test_tool_calling.py` asserts the round trip that replaced this refusal.

    What remains is the part shape that asks a *provider* to run something on our behalf, which is
    the `ADR-0013` boundary proper and does not move.
    """
    status, message = _post({"contents": [{"role": "user", "parts": [part]}]})
    assert status == 400


def test_a_request_for_several_candidates_is_refused_rather_than_answered_with_one() -> None:
    """`candidateCount: 3` returned one candidate and a 200. One answer where three were asked for
    does not look like a partial failure; it looks like the model had one thing to say."""
    status, message = _post(_base(candidateCount=3, maxOutputTokens=10))
    assert status == 400
    assert "candidateCount" in message


def test_a_single_candidate_is_the_one_value_that_is_accepted() -> None:
    """Refusing `candidateCount: 1` would break clients that state the default explicitly."""
    assert _post(_base(candidateCount=1, maxOutputTokens=10))[0] == 200


def test_thinking_config_at_the_top_level_is_refused_and_says_where_it_belongs() -> None:
    """Made while probing this very behaviour: `thinkingConfig` was written beside `contents`
    instead of inside `generationConfig`, and the gateway served the request with the model's own
    thinking mode and a 200. The mistake is easy, so the message names the fix."""
    status, message = _post({**_base(), "thinkingConfig": {"mode": "high"}})
    assert status == 400
    assert "generationConfig" in message


def test_a_field_nobody_has_ever_defined_is_refused_naming_it() -> None:
    """Google's own API does this. Being lenient here was `FRD-100` FR-7's compatibility argument,
    and it turned out to buy nothing: a client sending an unknown field is either misspelling a
    real one or using a feature we do not have, and both are better said out loud."""
    status, message = _post({**_base(), "quantumMode": True})
    assert status == 400
    assert "quantumMode" in message


def test_a_provider_adding_a_response_field_still_does_not_break_a_caller() -> None:
    """The strictness is one-directional on purpose. Requests are a promise we make; responses are
    a promise somebody else makes, and tightening both would turn every upstream release into an
    outage."""
    assert schemas.GeminiModel.model_config.get("extra") != "forbid"


# == portable and supported: carried to the dialect ===============================================


def test_every_sampling_control_reaches_the_canonical_request() -> None:
    canonical = gemini_to_canonical(
        "mock-1",
        schemas.GenerateContentRequest.model_validate(
            _base(
                topP=0.1,
                topK=5,
                seed=42,
                presencePenalty=1.5,
                frequencyPenalty=0.5,
                stopSequences=["END"],
            )
        ),
        bounds=SchemaBounds(),
    )
    assert canonical.top_p == 0.1
    assert canonical.top_k == 5
    assert canonical.seed == 42
    assert canonical.presence_penalty == 1.5
    assert canonical.frequency_penalty == 0.5
    assert canonical.stop_sequences == ("END",)


def test_only_what_was_asked_for_counts_as_requested() -> None:
    """A dialect without `top_k` must not refuse a request that never mentioned it — and an empty
    `stopSequences` is not a stop sequence."""
    request = CanonicalRequest(
        model="m", messages=[CanonicalMessage(role=Role.USER, text="hi")], stop_sequences=()
    )
    assert request.sampling_requested == frozenset()
    assert CanonicalRequest(
        model="m", messages=[CanonicalMessage(role=Role.USER, text="hi")], top_p=0.5
    ).sampling_requested == {"top_p"}


def _canonical(**kwargs: object) -> CanonicalRequest:
    return CanonicalRequest(
        model="m", messages=[CanonicalMessage(role=Role.USER, text="hi")], **kwargs
    )


def test_the_gemini_dialect_carries_all_six() -> None:
    body = canonical_to_gemini_request(
        _canonical(
            top_p=0.1,
            top_k=5,
            seed=42,
            presence_penalty=1.5,
            frequency_penalty=0.5,
            stop_sequences=("END",),
        )
    )
    config = body["generationConfig"]
    assert config["topP"] == 0.1
    assert config["topK"] == 5
    assert config["seed"] == 42
    assert config["presencePenalty"] == 1.5
    assert config["frequencyPenalty"] == 0.5
    assert config["stopSequences"] == ["END"]


def test_the_openai_dialect_carries_what_it_has_and_renames_stop() -> None:
    body = canonical_to_openai(
        _canonical(top_p=0.1, seed=42, presence_penalty=1.5, stop_sequences=("END",))
    )
    assert body["top_p"] == 0.1
    assert body["seed"] == 42
    assert body["presence_penalty"] == 1.5
    assert body["stop"] == ["END"]


def test_the_anthropic_dialect_carries_what_it_has() -> None:
    body = canonical_to_anthropic(
        _canonical(top_p=0.1, top_k=5, stop_sequences=("END",)), max_tokens=100
    )
    assert body["top_p"] == 0.1
    assert body["top_k"] == 5
    assert body["stop_sequences"] == ["END"]


# == the dialect cannot say it: refused, never dropped ============================================


def test_the_openai_dialect_refuses_top_k_rather_than_dropping_it() -> None:
    """The backstop behind the requirement. Both have to agree, and on the day they do not, this is
    the one holding the request."""
    with pytest.raises(DialectUnsupported, match="top_k"):
        canonical_to_openai(_canonical(top_k=5))


@pytest.mark.parametrize("field", ["seed", "presence_penalty", "frequency_penalty"])
def test_the_anthropic_dialect_refuses_what_the_messages_api_has_no_word_for(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        canonical_to_anthropic(_canonical(**{field: 1}), max_tokens=100)


class _Provider:
    #: A test double, like `MockProvider` (`FRD-307`): it serves invented models, so the
    #: catalogue-and-approve requirement does not apply to it.
    is_test_double = True

    def __init__(self, supported: frozenset[str]) -> None:
        self.sampling_controls = supported

    def models(self):  # noqa: ANN201
        from aira_gateway.upstreams.base import UpstreamModel

        return [UpstreamModel(name="m", version="1", supported_methods=("generateContent",))]


class _Undeclared(_Provider):
    #: A test double, like `MockProvider` (`FRD-307`): it serves invented models, so the
    #: catalogue-and-approve requirement does not apply to it.
    is_test_double = True
    """An adapter that forgot. `sampling_controls` is deliberately absent, not empty."""

    def __init__(self) -> None:
        pass


async def test_a_candidate_whose_dialect_cannot_express_the_control_is_skipped_by_name() -> None:
    registry = ProviderRegistry([_Provider(frozenset({"top_p"}))])
    requirement = SamplingExpressible(registry, frozenset({"top_p", "seed"}))

    refusal = await requirement.refusal("m")

    assert refusal is not None
    assert "seed" in refusal
    assert "top_p" not in refusal, "a control the dialect *has* must not appear in the reason"


async def test_a_candidate_that_can_express_everything_asked_for_is_not_skipped() -> None:
    registry = ProviderRegistry([_Provider(frozenset({"top_p", "seed"}))])
    assert await SamplingExpressible(registry, frozenset({"top_p"})).refusal("m") is None


async def test_a_request_that_sets_nothing_never_skips_anybody() -> None:
    registry = ProviderRegistry([_Provider(frozenset())])
    assert await SamplingExpressible(registry, frozenset()).refusal("m") is None


async def test_an_adapter_that_declares_nothing_refuses_rather_than_allows() -> None:
    """Undeclared means unsupported — the catalog's rule, applied to dialects. The alternative is
    that the one adapter somebody forgot is the one that silently drops everything."""
    registry = ProviderRegistry([_Undeclared()])
    refusal = await SamplingExpressible(registry, frozenset({"top_p"})).refusal("m")
    assert refusal is not None and "top_p" in refusal


def test_every_adapter_declares_its_sampling_support() -> None:
    """The check that makes the rule above a floor rather than a policy.

    Same shape as the architecture assertion in `test_vertex.py`: an adapter added without a
    declaration should fail here, at the point somebody can still choose the right answer, rather
    than in production by refusing every request that sets `top_p`.
    """
    from aira_gateway.upstreams.gemini import GeminiUpstream
    from aira_gateway.upstreams.mock import MockProvider
    from aira_gateway.upstreams.openai.adapter import OpenAIAdapter
    from aira_gateway.upstreams.vertex.adapters import VertexAnthropicAdapter, VertexGeminiAdapter

    adapters = [
        MockProvider,
        GeminiUpstream,
        OpenAIAdapter,
        VertexGeminiAdapter,
        VertexAnthropicAdapter,
    ]
    for adapter in adapters:
        declared = getattr(adapter, "sampling_controls", None)
        assert declared is not None, f"{adapter.__name__} declares no sampling support"
        unknown = set(declared) - set(SAMPLING_CONTROLS)
        assert not unknown, f"{adapter.__name__} declares controls nobody defines: {unknown}"


def test_the_declarations_differ_which_is_the_reason_they_are_declared() -> None:
    """If all three dialects supported the same set, this whole mechanism would be ceremony. They
    do not, and the differences are exactly where a silently dropped control would have lived."""
    assert "top_k" in GEMINI_SAMPLING and "top_k" not in OPENAI_SAMPLING
    assert "seed" in OPENAI_SAMPLING and "seed" not in ANTHROPIC_SAMPLING


def test_a_control_nobody_defined_cannot_be_requested() -> None:
    with pytest.raises(ValidationError):
        CanonicalRequest(
            model="m", messages=[CanonicalMessage(role=Role.USER, text="hi")], mirostat=2
        )


# == the compatibility surface holds the same rule ================================================


def test_the_kira_surface_names_an_unknown_field_rather_than_dropping_it() -> None:
    """The rule is *no silent drop*, and refusal was only ever one way of keeping it.

    `FRD-107` Stage A promised "an unsupported field is refused by name, never ignored", and the
    refusal half of that was measured against a real chatbot on 2026-08-18: it sends fields the
    predecessor tolerated, so every call came back `422` over a field that changes no answer. A
    compatibility surface that refuses the traffic it exists to accept is not one.

    What survives is the part that mattered — the field is **named**, in a header on the very
    response it affected. An operator can see that a client is sending `thinkingBudget` months
    before anybody wonders why thinking never happens, and the caller is told on the spot.

    The near-miss case is the exception and keeps its refusal: see
    `test_kira_field_spellings.py`, where accepting `conversationHistory` would answer without
    the conversation.
    """
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    with TestClient(app) as client:
        response = client.post(
            "/kira/api/external/chat",
            json={
                "request": {"parts": [{"text": "hi"}]},
                "model_id": 1,
                "topSecretTuning": 7,
            },
        )

    # The request gets *past* the field — this bare gateway has no catalogue, so it then fails on
    # the model id, and that is the refusal a caller sees. What matters is that the unknown field
    # is no longer the thing that stopped it, and that it was not swallowed on the way either:
    # the header names it, on the very response it travelled with.
    assert "topSecretTuning" not in response.text, response.text
    assert response.json()["code"] == "MODEL_NOT_FOUND"
    assert response.headers.get("X-AIRA-Unmodelled-Fields") == "topSecretTuning"


# == through the route, which is the only place the wiring is real ================================


class _Limited:
    """A provider whose dialect has `top_p` and nothing else."""

    #: A test double (`FRD-307`): it serves invented models, so the catalogue-and-approve
    #: requirement does not apply to it.
    is_test_double = True

    sampling_controls = frozenset({"top_p"})

    def __init__(self) -> None:
        self.reached = False

    def models(self):  # noqa: ANN201
        from aira_gateway.upstreams.base import UpstreamModel

        return [
            UpstreamModel(
                name="limited-1",
                version="1",
                supported_methods=("generateContent",),
                provider="test",
                region="eu",
            )
        ]

    async def generate(self, request):  # noqa: ANN001, ANN201
        from aira_gateway.core.canonical import CanonicalResponse, CanonicalUsage

        self.reached = True
        return CanonicalResponse(
            model=request.model,
            text="ok",
            finish_reason="stop",
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
        )


async def test_a_request_the_serving_dialect_cannot_express_fails_at_the_route() -> None:
    """The test the requirement's own unit tests cannot replace, and a mutation proved it: they
    exercise `SamplingExpressible` directly, so removing it from the route's requirement list left
    every one of them green. Two correct halves and no wire between them — the same gap the export
    had, in a different file, found the same way.

    The mock provider expresses everything, so a request through the default app can never be
    refused; a provider that cannot is the only way to see the wiring at all.
    """
    provider = _Limited()
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0, allowed_regions="eu"))
    app.state.providers = ProviderRegistry([provider])

    with TestClient(app) as client:
        response = client.post(
            "/v1beta/models/limited-1:generateContent",
            json=_base(topP=0.5, topK=5, maxOutputTokens=10),
        )

    assert not provider.reached, "the request reached a provider that cannot express it"

    assert response.status_code == 400
    assert response.json()["error"]["status"] == "FAILED_PRECONDITION"
    message = response.json()["error"]["message"]
    assert "top_k" in message
    assert "top_p" not in message, "the reason names a control the dialect has"


async def test_a_request_using_only_what_the_dialect_has_still_reaches_the_provider() -> None:
    """The other half: a requirement that refused everything would also pass the test above."""
    provider = _Limited()
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0, allowed_regions="eu"))
    app.state.providers = ProviderRegistry([provider])

    with TestClient(app) as client:
        response = client.post(
            "/v1beta/models/limited-1:generateContent", json=_base(topP=0.5, maxOutputTokens=10)
        )

    assert response.status_code == 200
    assert provider.reached


# == the second axis: what a dialect can say about thinking ======================================


def _adapters() -> list[type]:
    from aira_gateway.upstreams.gemini import GeminiUpstream
    from aira_gateway.upstreams.mock import MockProvider
    from aira_gateway.upstreams.openai.adapter import OpenAIAdapter
    from aira_gateway.upstreams.vertex.adapters import VertexAnthropicAdapter, VertexGeminiAdapter

    return [
        MockProvider,
        GeminiUpstream,
        OpenAIAdapter,
        VertexGeminiAdapter,
        VertexAnthropicAdapter,
    ]


def test_every_adapter_declares_its_thinking_support() -> None:
    """The same rule as sampling, on the axis that had none.

    Vendors differ about thinking in two ways, and only one of them was ever written down. The
    **envelope** — which modes a model offers, what a level costs — is per model and lives in the
    catalogue. The **shape** is per dialect: Gemini and Anthropic take a token budget, the OpenAI
    dialect takes a word and has no budget at all.

    The shape was scattered across `if` branches, and the two dialects that cannot express
    something behaved differently about it: one raised, and the other **omitted the field**. A
    caller asking Anthropic for `auto` with no resolved budget received a body identical to
    `disabled`, answered `200`, and was told nothing. That is the failure this file is named for,
    on the one axis it did not cover.

    Declared per adapter, never defaulted to "all" — so the sixth dialect has to answer the
    question at the point somebody can still choose the right answer.
    """
    from aira_common.models import ThinkingMode

    for adapter in _adapters():
        declared = getattr(adapter, "thinking_modes", None)
        assert declared is not None, f"{adapter.__name__} declares no thinking support"
        unknown = set(declared) - set(ThinkingMode)
        assert not unknown, f"{adapter.__name__} declares modes nobody defines: {unknown}"
        # **The second half, added with `ADR-0021`.** A level stopped being a member of the enum
        # above and became a word the vendor accepts, so "can this dialect express a level" is no
        # longer answerable from the mode set — and it is a different answer per dialect: two of
        # them have a field that takes a word and one has only a number.
        assert isinstance(getattr(adapter, "expresses_thinking_levels", None), bool), (
            f"{adapter.__name__} does not say whether it has a field for a level word"
        )


def test_the_thinking_declarations_differ_which_is_the_reason_they_are_declared() -> None:
    """If every dialect took the same vocabulary this mechanism would be ceremony. They do not,
    and the differences are exactly where a silently dropped mode lived."""
    from aira_common.models import ThinkingMode
    from aira_gateway.upstreams.openai.adapter import OpenAIAdapter
    from aira_gateway.upstreams.vertex.adapters import VertexAnthropicAdapter, VertexGeminiAdapter

    # No token budget in this dialect, so a caller's explicit count cannot be honoured exactly —
    # and no way to say "you decide" either, since `reasoning_effort` is always a level.
    assert ThinkingMode.LIMITED not in OpenAIAdapter.thinking_modes
    assert ThinkingMode.AUTO not in OpenAIAdapter.thinking_modes
    # No "decide for yourself" value in this one.
    assert ThinkingMode.AUTO not in VertexAnthropicAdapter.thinking_modes
    # And the budget-shaped Gemini dialect can say all three.
    assert VertexGeminiAdapter.thinking_modes == frozenset(ThinkingMode)

    # The other axis, and it splits the dialects differently — which is the argument for it being
    # its own declaration rather than something read off the mode set.
    assert OpenAIAdapter.expresses_thinking_levels is True
    assert VertexGeminiAdapter.expresses_thinking_levels is True
    assert VertexAnthropicAdapter.expresses_thinking_levels is False


def test_a_mode_a_dialect_cannot_express_is_refused_and_never_omitted() -> None:
    """The defect itself, on the dialect that had it.

    Measured before the fix: `auto`, `high` and `low` with no resolved budget each produced **no
    `thinking` block at all** — the same body as `disabled`. Silence is the one answer a control
    plane cannot give about a control.
    """
    from aira_gateway.core.canonical import Thinking
    from aira_gateway.upstreams.base import DialectUnsupported
    from aira_gateway.upstreams.vertex.anthropic_mapping import canonical_to_anthropic

    asked = CanonicalRequest(
        model="claude",
        messages=[CanonicalMessage(role=Role.USER, text="hi")],
        thinking=Thinking(mode="high"),
    )

    with pytest.raises(DialectUnsupported) as caught:
        canonical_to_anthropic(asked, max_tokens=4096)

    assert "budget" in str(caught.value)


def test_a_candidate_that_cannot_be_addressed_is_skipped_not_a_server_error() -> None:
    """Since cataloguing became enough to serve a model, the address can be wrong in two new ways.

    A platform that needs a region and an entry that names none; a region outside
    `AIRA_ALLOWED_REGIONS`. Both are **an operator's** mistake and both escaped the dispatch chain
    as a `500` — measured on 2026-08-19 against a real deployment, twice. A configuration fault
    dressed as our fault is the shape this file exists for, arriving through a door that only
    opened when the catalogue became the list.

    Skipped rather than raised, so a fallback chain moves on — that is what a chain is for — and so
    the refusal names the model and the reason when nothing else qualifies.
    """
    import asyncio

    from aira_gateway.pipeline.dispatch import NoCapableModel, dispatch_with_fallback
    from aira_gateway.residency import RegionNotAllowed
    from aira_gateway.upstreams.base import ProviderRegistry

    class _Unaddressable:
        is_test_double = True
        sampling_controls = frozenset()
        thinking_modes = frozenset()
        serves_provider = "nowhere"

        def models(self):
            return []

        async def generate(self, request):
            raise RegionNotAllowed("Region 'us-central1' is not in the allowed set")

    registry = ProviderRegistry([_Unaddressable()])

    async def declared(_model: str) -> tuple[str, str]:
        return "nowhere", ""

    with pytest.raises(NoCapableModel) as caught:
        asyncio.run(
            dispatch_with_fallback(
                registry,
                CanonicalRequest(model="m", messages=[CanonicalMessage(role=Role.USER, text="hi")]),
                (),
                provider_of=declared,
            )
        )

    assert "us-central1" in str(caught.value)
