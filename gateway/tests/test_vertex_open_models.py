"""Agent Platform's managed open models — GLM, DeepSeek — on the Vertex transport (`FRD-115` FR-2a).

Hermetic: an ``httpx.MockTransport`` stands in for Google and answers in the shapes the endpoint
sends — every stream chunk carrying a `usage` block with no count in it, the thinking in
`reasoning_content`, an error object wrapped in a list. Those three are what this dialect had not
met before, and each would have been wrong quietly: a cancelled stream booked as zero tokens, the
reasoning a use case asked for never arriving, the reason for a `400` lost.

The other half of the feature is a policy one. These models answer at `global` and nowhere else,
and `global` names no region; so an installation permits it **for them, by name**
(`AIRA_ALLOWED_MODEL_REGIONS`) and for no other model (`FRD-611`).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from aira_common.models import ThinkingMode
from aira_common.tokens import StaticTokenSource
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalEmbeddingRequest,
    CanonicalMessage,
    CanonicalRequest,
    FinishReason,
    Role,
    Thinking,
    ToolDeclaration,
)
from aira_gateway.core.schema import parse as parse_schema
from aira_gateway.residency import RegionNotAllowed
from aira_gateway.upstreams.base import (
    DialectUnsupported,
    ProviderRegistry,
    UpstreamError,
    can_count,
)
from aira_gateway.upstreams.openai.mapping import SAMPLING as OPENAI_SAMPLING
from aira_gateway.upstreams.vertex import build_vertex_upstreams
from aira_gateway.upstreams.vertex.adapters import (
    OPEN_MODEL_PUBLISHERS,
    VertexGeminiAdapter,
    VertexModel,
    VertexOpenModelAdapter,
)
from aira_gateway.upstreams.vertex.transport import VertexTransport

GLM = "glm-4.7-maas"
DEEPSEEK = "deepseek-v3.2-maas"
ENDPOINT = (
    "https://aiplatform.googleapis.com/v1/projects/my-project/locations/global"
    "/endpoints/openapi/chat/completions"
)
#: What the endpoint puts on every chunk of a stream before the last: a `usage` with no count.
TRAFFIC_ONLY = {"extra_properties": {"google": {"traffic_type": "ON_DEMAND"}}}


def _request(model: str = GLM, **over: Any) -> CanonicalRequest:
    return CanonicalRequest(
        model=model,
        messages=[
            CanonicalMessage(role=Role.SYSTEM, text="be brief"),
            CanonicalMessage(role=Role.USER, text="hello"),
        ],
        **over,
    )


def _answer(**message: Any) -> dict[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "index": 0,
                "message": {"role": "assistant", "content": "OK", "tool_calls": None, **message},
            }
        ],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 61,
            "total_tokens": 73,
            "prompt_tokens_details": {"cached_tokens": 8},
            **TRAFFIC_ONLY,
        },
    }


class _Google:
    """Records what was sent, and answers with whatever the test hands it."""

    def __init__(self, answer: httpx.Response | None = None) -> None:
        self.sent: list[httpx.Request] = []
        self._answer = answer or httpx.Response(200, json=_answer())

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        return self._answer

    def body(self, index: int = -1) -> dict[str, Any]:
        return json.loads(self.sent[index].content)


def _transport(google: _Google, *, permitted: dict[str, frozenset[str]] | None = None) -> Any:
    return VertexTransport(
        project="my-project",
        tokens=StaticTokenSource("test-token"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(google)),
        model_regions=(
            {GLM: frozenset({"global"}), DEEPSEEK: frozenset({"global"})}
            if permitted is None
            else permitted
        ),
    )


def _adapter(
    google: _Google,
    publisher: str = "zai-org",
    *,
    configured: list[VertexModel] | None = None,
    permitted: dict[str, frozenset[str]] | None = None,
) -> VertexOpenModelAdapter:
    return VertexOpenModelAdapter(
        _transport(google, permitted=permitted),
        configured if configured is not None else [VertexModel("global", "zai-org", GLM)],
        publisher=publisher,
        thinking_switch=OPEN_MODEL_PUBLISHERS[publisher],
    )


def _stream(*events: dict[str, Any]) -> httpx.Response:
    lines = [f"data: {json.dumps(event)}\n\n" for event in events] + ["data: [DONE]\n\n"]
    return httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content="".join(lines).encode()
    )


def _delta(**delta: Any) -> dict[str, Any]:
    return {
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        "usage": TRAFFIC_ONLY,
    }


def _finish(reason: str) -> dict[str, Any]:
    return {
        "choices": [{"index": 0, "delta": {"content": None}, "finish_reason": reason}],
        "usage": TRAFFIC_ONLY,
    }


_USAGE_CHUNK = {
    "choices": [],
    "usage": {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14, **TRAFFIC_ONLY},
}


# == where a request goes =========================================================================


async def test_one_endpoint_serves_every_publisher_and_the_body_names_the_model() -> None:
    """The model is not in the path here, as it is for Gemini and Claude: the endpoint is one
    OpenAI-compatible path and the body says ``publisher/model``. A caller names the model alone,
    and the gateway writes the publisher it is configured with."""
    google = _Google()

    await _adapter(google).generate(_request())

    sent = google.sent[0]
    assert str(sent.url) == ENDPOINT
    assert sent.headers["Authorization"] == "Bearer test-token"
    body = google.body()
    assert body["model"] == f"zai-org/{GLM}"
    assert body["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hello"},
    ]


async def test_a_catalogued_model_is_reached_with_its_publisher_without_configuration() -> None:
    """Cataloguing is enough (`FRD-507`): the catalogue's region and the adapter's publisher are
    the whole address, so no `AIRA_VERTEX_MODELS` entry is needed."""
    google = _Google()
    adapter = _adapter(google, "deepseek-ai", configured=[])

    answer = await adapter.generate(_request(DEEPSEEK, addressing={"regions": ["global"]}))

    assert google.body()["model"] == f"deepseek-ai/{DEEPSEEK}"
    assert answer.served_region == "global"


async def test_global_is_refused_for_a_model_nobody_permitted_it_for() -> None:
    """The policy, not the adapter, decides — and without a permission by name it says no before a
    byte leaves: `global` is not in any default, and these models are served nowhere else."""
    google = _Google()

    with pytest.raises(RegionNotAllowed, match="'global'"):
        await _adapter(google, permitted={}).generate(_request())

    assert google.sent == []


async def test_a_permission_by_name_is_for_that_name_alone() -> None:
    """Permitting `global` for GLM is a decision about GLM. A second model catalogued at the same
    place has to be named too, or the exception has become the list."""
    google = _Google()
    adapter = _adapter(google, "deepseek-ai", configured=[], permitted={GLM: frozenset({"global"})})

    with pytest.raises(RegionNotAllowed, match=f"'{DEEPSEEK}'"):
        await adapter.generate(_request(DEEPSEEK, addressing={"regions": ["global"]}))

    assert google.sent == []


# == thinking: a switch, spelled per publisher ====================================================


@pytest.mark.parametrize(
    ("publisher", "mode", "expected"),
    [
        ("zai-org", ThinkingMode.DISABLED, {"enable_thinking": False}),
        ("zai-org", ThinkingMode.AUTO, {"enable_thinking": True}),
        ("deepseek-ai", ThinkingMode.AUTO, {"thinking": True}),
        ("deepseek-ai", ThinkingMode.DISABLED, {"thinking": False}),
    ],
)
async def test_thinking_is_switched_the_way_each_publisher_reads_it(
    publisher: str, mode: ThinkingMode, expected: dict[str, bool]
) -> None:
    """GLM thinks unless told not to and DeepSeek only when told to, through different template
    keys. Sent the other one's key, each would ignore it and think — or not — by its own default,
    and bill accordingly."""
    google = _Google()
    model = GLM if publisher == "zai-org" else DEEPSEEK
    adapter = _adapter(google, publisher, configured=[])

    await adapter.generate(
        _request(model, addressing={"regions": ["global"]}, thinking=Thinking(mode=mode))
    )

    body = google.body()
    assert body["chat_template_kwargs"] == expected
    # The dialect's own word for it is not sent: no model here documents it.
    assert "reasoning_effort" not in body


async def test_a_request_that_says_nothing_about_thinking_sends_no_switch() -> None:
    google = _Google()

    await _adapter(google).generate(_request())

    assert "chat_template_kwargs" not in google.body()
    assert "reasoning_effort" not in google.body()


@pytest.mark.parametrize(
    "setting",
    [Thinking(mode=ThinkingMode.LIMITED, tokens=512), Thinking(mode="high")],
    ids=["a budget", "a level word"],
)
async def test_a_budget_or_a_level_is_refused_rather_than_rounded(setting: Thinking) -> None:
    """The switch has two positions. A budget or a level mapped onto one of them would spend a
    different amount than was asked for, under a 200."""
    google = _Google()

    with pytest.raises(DialectUnsupported):
        await _adapter(google).generate(_request(thinking=setting))

    assert google.sent == []


def test_the_declarations_say_what_the_wire_can_express() -> None:
    """Read by the chain before dispatch (`FRD-111` §5.2, `FRD-124`), so a request this wire cannot
    carry skips the candidate by name instead of reaching the backstop above."""
    adapter = _adapter(_Google())

    assert adapter.thinking_modes == frozenset({ThinkingMode.DISABLED, ThinkingMode.AUTO})
    assert adapter.expresses_thinking_levels is False
    assert adapter.sampling_controls == OPENAI_SAMPLING
    assert adapter.speaks is False


# == what comes back ==============================================================================


@pytest.mark.parametrize("asked", [True, False], ids=["asked", "did-not-ask"])
async def test_the_reasoning_reaches_exactly_the_use_cases_that_asked(asked: bool) -> None:
    """These models send their thinking in `reasoning_content` whether or not anybody asked
    (`FRD-135` FR-3), and it is never the answer."""
    google = _Google(httpx.Response(200, json=_answer(reasoning_content="The user wants OK.")))

    answer = await _adapter(google).generate(_request(include_reasoning=asked))

    assert answer.text == "OK"
    assert answer.reasoning == ("The user wants OK." if asked else "")


async def test_the_answer_says_what_it_used_and_where_it_was_produced() -> None:
    answer = await _adapter(_Google()).generate(_request())

    assert answer.usage.prompt_tokens == 12
    assert answer.usage.completion_tokens == 61
    assert answer.usage.cached_input_tokens == 8
    # The residency evidence on the audit row (`FRD-115` FR-10).
    assert answer.served_region == "global"


async def test_a_refusal_keeps_the_reason_the_endpoint_gave() -> None:
    """The error object arrives inside a one-element list here. Read as an object, the reason for
    every `400` was lost — the most actionable thing a caller is told about a body we built."""
    refusal = httpx.Response(
        400,
        json=[
            {
                "error": {
                    "code": 400,
                    "message": "Expected input to contain field: 'messages'.",
                    "status": "INVALID_ARGUMENT",
                }
            }
        ],
    )

    with pytest.raises(UpstreamError) as caught:
        await _adapter(_Google(refusal)).generate(_request())

    assert caught.value.status_code == 400
    assert "Expected input to contain field: 'messages'." in caught.value.message


# == a stream =====================================================================================


async def test_a_stream_reports_its_usage_once_and_only_where_it_has_counts() -> None:
    """Every chunk before the last carries a `usage` holding nothing but a traffic type. Read as a
    report it is zero tokens — and a stream whose caller left after the second chunk would be booked
    at zero, as a running total, rather than as unreported."""
    google = _Google(
        _stream(
            _delta(role="assistant", content="", reasoning_content=None),
            _delta(content="O", reasoning_content=None),
            _delta(content="K", reasoning_content=None),
            _finish("stop"),
            _USAGE_CHUNK,
        )
    )

    chunks = [chunk async for chunk in _adapter(google).stream_generate(_request())]

    assert "".join(chunk.text_delta for chunk in chunks) == "OK"
    reported = [chunk.usage for chunk in chunks if chunk.usage is not None]
    assert len(reported) == 1
    assert (reported[0].prompt_tokens, reported[0].completion_tokens) == (12, 2)
    assert chunks[0].served_region == "global"
    assert google.body()["stream"] is True
    assert google.body()["stream_options"] == {"include_usage": True}
    assert google.body()["model"] == f"zai-org/{GLM}"


async def test_a_streamed_tool_call_is_assembled_from_its_fragments() -> None:
    """The first fragment carries the id and the name, the rest only pieces of the arguments with
    `null` where the id and the name were. Emitted whole, on the chunk that ends the message."""
    weather = ToolDeclaration(
        name="get_weather",
        description="Current weather for a city.",
        parameters=parse_schema(
            {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
        ),
    )

    def fragment(arguments: str, **head: Any) -> dict[str, Any]:
        call = {
            "index": 0,
            "type": "function",
            "id": head.get("id"),
            "function": {"name": head.get("name"), "arguments": arguments},
        }
        return _delta(content=None, tool_calls=[call])

    google = _Google(
        _stream(
            _delta(role="assistant", content=""),
            fragment("", id="call_1", name="get_weather"),
            fragment("{"),
            fragment('"city": "Berlin"'),
            fragment("}"),
            _finish("tool_calls"),
            _USAGE_CHUNK,
        )
    )

    chunks = [chunk async for chunk in _adapter(google).stream_generate(_request(tools=(weather,)))]

    calls = [call for chunk in chunks for call in chunk.tool_calls]
    assert [(call.id, call.name, call.arguments) for call in calls] == [
        ("call_1", "get_weather", {"city": "Berlin"})
    ]
    finished = next(chunk for chunk in chunks if chunk.finish_reason is not None)
    assert finished.finish_reason == FinishReason.TOOL_USE
    assert finished.tool_calls == tuple(calls)
    assert google.body()["tools"][0]["function"]["name"] == "get_weather"


async def test_a_stream_refused_before_its_first_byte_is_an_upstream_failure() -> None:
    """So the chain can still move on to another candidate (`ADR-0032`)."""
    google = _Google(httpx.Response(429, json=[{"error": {"code": 429, "status": "QUOTA"}}]))

    with pytest.raises(UpstreamError) as caught:
        async for _ in _adapter(google).stream_generate(_request()):
            pass

    assert caught.value.status_code == 429


# == asking without spending ======================================================================


async def test_the_probe_asks_with_an_empty_conversation_and_reads_a_400_as_there() -> None:
    """There is no free count for these models, and their listing takes no API key. The endpoint
    looks a model up before it reads the body, so an empty conversation is refused `400` for a
    model that is there — and nothing is generated, so nothing is billed."""
    google = _Google(
        httpx.Response(400, json=[{"error": {"code": 400, "message": "Messages cannot be empty."}}])
    )

    verdict = await _adapter(google).ping(GLM, {"regions": ["global"]})

    assert verdict == f"{GLM} answered"
    assert google.body() == {"model": f"zai-org/{GLM}", "messages": []}
    assert str(google.sent[0].url) == ENDPOINT


async def test_the_probe_reports_a_model_that_is_not_there() -> None:
    google = _Google(httpx.Response(404, json=[{"error": {"code": 404, "status": "NOT_FOUND"}}]))

    with pytest.raises(UpstreamError) as caught:
        await _adapter(google).ping(GLM, {"regions": ["global"]})

    assert caught.value.status_code == 404


async def test_the_probe_checks_residency_like_a_request() -> None:
    """A probe sends the model's name to the place it asks about; a place nobody permitted is not
    contacted to find out."""
    google = _Google()

    with pytest.raises(RegionNotAllowed):
        await _adapter(google, permitted={}).ping(GLM, {"regions": ["global"]})

    assert google.sent == []


def test_a_cancelled_stream_stays_unreported_rather_than_counted() -> None:
    """These models have no free count (`:countTokens` answers `404`), so a stream whose caller
    left before the last chunk is booked as unreported — never at a figure worked out here."""
    assert not can_count(_adapter(_Google()))


async def test_an_embedding_is_refused_as_a_backstop() -> None:
    with pytest.raises(UpstreamError) as caught:
        await _adapter(_Google()).embed(CanonicalEmbeddingRequest(model=GLM, texts=["x"]))

    assert caught.value.status_code == 400


# == registration =================================================================================


def _settings(**over: Any) -> GatewaySettings:
    return GatewaySettings(vertex_project="p", vertex_api_key="AQ.key", **over)


def test_each_open_model_publisher_has_an_adapter_the_catalogue_routes_by() -> None:
    """The catalogue's ``(provider, publisher)`` decides the wire (`FRD-507`): a GLM model
    catalogued under `vertex` and `zai-org` reaches the OpenAI dialect, a Gemini one the Gemini
    dialect — and a publisher nothing here speaks reaches nothing rather than the wrong one."""
    registry = ProviderRegistry(build_vertex_upstreams(_settings()))

    glm = registry.provider_for(GLM, "vertex", "zai-org")
    deepseek = registry.provider_for(DEEPSEEK, "vertex", "deepseek-ai")
    assert isinstance(glm, VertexOpenModelAdapter)
    assert glm.serves_publisher == "zai-org"
    assert isinstance(deepseek, VertexOpenModelAdapter)
    assert deepseek.serves_publisher == "deepseek-ai"
    gemini = registry.provider_for("gemini-2.5-flash", "vertex", "google")
    assert isinstance(gemini, VertexGeminiAdapter)
    assert registry.provider_for("minimax-m2-maas", "vertex", "minimaxai") is None


def test_a_configured_open_model_at_global_needs_its_permission_at_startup() -> None:
    """Startup is where a configured model's residency is decided (`FRD-115` FR-5): refused without
    the permission by name, served with it."""
    with pytest.raises(RegionNotAllowed, match=GLM):
        build_vertex_upstreams(_settings(vertex_models=f"global/zai-org/{GLM}"))

    upstreams = build_vertex_upstreams(
        _settings(vertex_models=f"global/zai-org/{GLM}", allowed_model_regions=f"global={GLM}")
    )

    served = {model.name: model for upstream in upstreams for model in upstream.models()}
    assert (served[GLM].provider, served[GLM].publisher, served[GLM].region) == (
        "vertex",
        "zai-org",
        "global",
    )


def test_a_configured_model_from_a_publisher_nothing_here_speaks_stops_the_start() -> None:
    """It used to be dropped: configured, never served, and answered "not found" — which sends
    whoever debugs it to the catalogue rather than to the line in the environment."""
    with pytest.raises(ValueError, match="'minimaxai'"):
        build_vertex_upstreams(
            _settings(
                vertex_models="global/minimaxai/minimax-m2-maas",
                allowed_model_regions="global=minimax-m2-maas",
            )
        )
