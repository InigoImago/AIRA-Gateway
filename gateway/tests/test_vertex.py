"""Reaching Vertex AI in the EU, and the second wire dialect (FRD-115, FRD-119).

Hermetic throughout: an ``httpx.MockTransport`` stands in for Google, so the URL construction, the
auth header, the body mapping and the error handling are all exercised without a project. The one
thing that cannot be tested here is whether Google agrees with our URLs, which is what the
integration layer is for.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

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
    ToolCallPart,
    ToolDeclaration,
    ToolResultPart,
)
from aira_gateway.core.schema import parse as parse_schema
from aira_gateway.core.schema import to_json_schema
from aira_gateway.residency import RegionNotAllowed
from aira_gateway.upstreams.base import (
    AmbiguousModel,
    DialectUnsupported,
    ProviderRegistry,
    UpstreamError,
    UpstreamModel,
)
from aira_gateway.upstreams.exchange import refused
from aira_gateway.upstreams.vertex import build_vertex_upstreams
from aira_gateway.upstreams.vertex.adapters import (
    VertexAnthropicAdapter,
    VertexGeminiAdapter,
    VertexModel,
)
from aira_gateway.upstreams.vertex.anthropic_mapping import (
    SCHEMA_UNSATISFIED,
    StreamAssembler,
    answer_text,
    anthropic_to_canonical,
    canonical_to_anthropic,
    usage_of,
)
from aira_gateway.upstreams.vertex.auth import CredentialsInvalid, ServiceAccount
from aira_gateway.upstreams.vertex.transport import NAME, VertexTransport, host_for

#: A key that can actually sign, as Google issues one: the startup check signs with it, so a
#: placeholder string would be refused before the property a test is about is reached.
_PRIVATE_KEY = (
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    .decode("ascii")
)
_SERVICE_ACCOUNT = json.dumps({"client_email": "a@b", "private_key": _PRIVATE_KEY})


def _request(model: str = "claude-1", **over: Any) -> CanonicalRequest:
    return CanonicalRequest(
        model=model,
        messages=[
            CanonicalMessage(role=Role.SYSTEM, text="be brief"),
            CanonicalMessage(role=Role.USER, text="hello"),
        ],
        **over,
    )


def _transport(handler, **over: Any) -> VertexTransport:  # noqa: ANN001
    return VertexTransport(
        project="my-project",
        tokens=StaticTokenSource("test-token"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **over,
    )


# -- the endpoint ---------------------------------------------------------------------------


def test_a_regional_host_and_the_eu_multi_region_differ() -> None:
    assert host_for("europe-west1") == "europe-west1-aiplatform.googleapis.com"
    # The multi-region has its own host rather than a `{region}-` prefix — getting this wrong
    # produces a DNS failure that reads like a network problem.
    assert host_for("eu") == "aiplatform.eu.rep.googleapis.com"


def test_the_url_names_the_project_region_publisher_and_method() -> None:
    url = _transport(lambda request: httpx.Response(200)).url(
        region="eu", publisher="anthropic", model="claude-sonnet-4-5@20250929", method="rawPredict"
    )

    assert url == (
        "https://aiplatform.eu.rep.googleapis.com/v1/projects/my-project"
        "/locations/eu/publishers/anthropic/models/claude-sonnet-4-5@20250929:rawPredict"
    )


def test_a_model_name_with_a_version_suffix_survives_the_url() -> None:
    """Anthropic ids carry `@20250929`. That is exactly the kind of character that turns out to
    be a problem in one place nobody checked."""
    url = _transport(lambda request: httpx.Response(200)).url(
        region="eu", publisher="anthropic", model="claude-sonnet-4-5@20250929", method="rawPredict"
    )
    assert "claude-sonnet-4-5@20250929" in url


# -- residency, enforced --------------------------------------------------------------------


def test_a_region_outside_the_allowed_set_is_refused() -> None:
    transport = _transport(lambda r: httpx.Response(200), allowed_regions=("eu",))

    with pytest.raises(RegionNotAllowed) as caught:
        transport.url(region="us-central1", publisher="google", model="m", method="x")

    assert "us-central1" in str(caught.value)


def test_a_deployment_refuses_to_start_with_a_model_outside_the_eu() -> None:
    """Configuration alone would not hold: someone adds a model in `us-central1` because that is
    where a preview launched, and nothing objects. Failing to boot is the correct response to a
    configuration that cannot honour its own residency claim."""
    settings = GatewaySettings(
        vertex_project="p",
        vertex_credentials=json.dumps({"client_email": "a@b", "private_key": "x"}),
        vertex_models="us-central1/google/gemini-2.5-pro",
    )

    with pytest.raises(RegionNotAllowed):
        build_vertex_upstreams(settings)


def test_an_eu_model_configures_normally() -> None:
    settings = GatewaySettings(
        vertex_project="p",
        vertex_credentials=_SERVICE_ACCOUNT,
        vertex_models="eu/google/gemini-2.5-pro,eu/anthropic/claude-sonnet-4-5@20250929",
    )

    upstreams = build_vertex_upstreams(settings)

    # One per dialect, and the open models' one per publisher, since the publisher is half of the
    # name the endpoint is given.
    assert len(upstreams) == 4
    names = {model.name for upstream in upstreams for model in upstream.models()}
    assert names == {"gemini-2.5-pro", "claude-sonnet-4-5@20250929"}


def test_an_unconfigured_deployment_registers_nothing() -> None:
    assert build_vertex_upstreams(GatewaySettings()) == []


def test_a_malformed_model_spec_is_refused_at_startup() -> None:
    settings = GatewaySettings(
        vertex_project="p",
        vertex_credentials=json.dumps({"client_email": "a@b", "private_key": "x"}),
        vertex_models="gemini-2.5-pro",  # no region, no publisher
    )

    with pytest.raises(ValueError, match="region/publisher/model"):
        build_vertex_upstreams(settings)


# -- an ambiguous routing table is a startup failure ----------------------------------------


class _Offering:
    def __init__(self, *names: str) -> None:
        self._names = names

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel(name, name, ("generateContent",)) for name in self._names]

    async def generate(self, request): ...  # noqa: ANN001, ANN201
    async def stream_generate(self, request): ...  # noqa: ANN001, ANN201
    async def embed(self, request): ...  # noqa: ANN001, ANN201


def test_two_providers_offering_one_model_refuse_to_start() -> None:
    """With one adapter, last-registration-wins was harmless. With three it becomes a silent
    decision about which region and which credential handled a request — invisible in every log
    and every report."""
    with pytest.raises(AmbiguousModel) as caught:
        ProviderRegistry([_Offering("gemini-2.5-pro"), _Offering("gemini-2.5-pro")])

    assert "gemini-2.5-pro" in str(caught.value)


# -- credentials -------------------------------------------------------------------------------


def test_credentials_that_are_not_json_are_refused_by_name() -> None:
    with pytest.raises(CredentialsInvalid):
        ServiceAccount.from_json("not json at all")


def test_a_missing_field_is_named_but_the_key_is_not() -> None:
    """The message is a log line, and a private key must not become one."""
    secret = "-----BEGIN PRIVATE KEY-----very-secret-----END PRIVATE KEY-----"
    with pytest.raises(CredentialsInvalid) as caught:
        ServiceAccount.from_json(json.dumps({"private_key": secret}))

    assert "client_email" in str(caught.value)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("raw", ["[]", '"a string"', "42"])
def test_credentials_that_are_json_but_not_an_object_are_refused_by_name(raw: str) -> None:
    """Valid JSON is not a key: a list crashed startup with an `AttributeError` that named neither
    the setting nor the problem."""
    settings = GatewaySettings(vertex_project="p", vertex_credentials=raw)

    with pytest.raises(CredentialsInvalid, match="JSON object"):
        build_vertex_upstreams(settings)


def test_a_key_that_cannot_sign_is_refused_at_startup_not_at_the_first_request() -> None:
    """**The case `CredentialsInvalid` exists for**: a garbled PEM passed startup, and every
    request then answered 503 "credentials unavailable" — what looks like an upstream outage. The
    key is used once while starting, so the gateway refuses to start instead, and says why without
    repeating the key."""
    settings = GatewaySettings(
        vertex_project="p",
        vertex_credentials=json.dumps({"client_email": "a@b", "private_key": "not a pem secret"}),
    )

    with pytest.raises(CredentialsInvalid, match="cannot sign") as caught:
        build_vertex_upstreams(settings)

    assert "not a pem secret" not in str(caught.value)


def test_a_key_that_signs_is_accepted() -> None:
    """The other half: the check must not refuse the credential Google actually issues."""
    assert ServiceAccount.from_json(_SERVICE_ACCOUNT).client_email == "a@b"


async def test_a_credential_that_fails_is_the_operators_to_read_not_the_callers() -> None:
    """A token exchange that fails says why — an expired key, a clock off by minutes — and that is
    this installation's business: the caller gets a 503 that says the credential is unavailable,
    and the cause goes to the log."""
    from aira_common.tokens import TokenSource

    class _Refuses:
        async def acquire(self, now: float) -> Any:
            raise CredentialsInvalid(
                "Token exchange failed with 400. A clock more than a few minutes off is the "
                "usual cause."
            )

    transport = VertexTransport(
        project="p",
        tokens=TokenSource(_Refuses()),
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
    )

    with pytest.raises(UpstreamError) as caught:
        await transport.post("https://eu-aiplatform.googleapis.com/v1/x", {})

    assert caught.value.status_code == 503
    assert "credentials are unavailable" in caught.value.message
    assert "Token exchange" not in caught.value.message
    assert "clock" not in caught.value.message


# -- the Anthropic dialect -----------------------------------------------------------------


def test_roles_and_the_system_prompt_map_to_anthropics_shape() -> None:
    body = canonical_to_anthropic(_request(), max_tokens=1024)

    assert body["system"] == "be brief", "system is a top-level parameter, not a message"
    assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
    assert body["max_tokens"] == 1024
    assert body["anthropic_version"]


def test_several_system_messages_are_concatenated_rather_than_dropped() -> None:
    """Anthropic takes one and the canonical model permits several. Keeping only the last would
    silently discard an instruction the caller gave."""
    request = CanonicalRequest(
        model="claude-1",
        messages=[
            CanonicalMessage(role=Role.SYSTEM, text="be brief"),
            CanonicalMessage(role=Role.SYSTEM, text="answer in German"),
            CanonicalMessage(role=Role.USER, text="hi"),
        ],
    )

    assert (
        canonical_to_anthropic(request, max_tokens=64)["system"] == "be brief\n\nanswer in German"
    )


def test_the_model_role_becomes_assistant() -> None:
    request = CanonicalRequest(
        model="claude-1",
        messages=[
            CanonicalMessage(role=Role.USER, text="hi"),
            CanonicalMessage(role=Role.MODEL, text="hello"),
        ],
    )

    roles = [
        message["role"] for message in canonical_to_anthropic(request, max_tokens=64)["messages"]
    ]
    assert roles == ["user", "assistant"]


def test_thinking_blocks_are_dropped_from_the_answer() -> None:
    """The obvious implementation concatenates every content block, and it is the wrong one: with
    thinking enabled it returns the model's reasoning to the caller, into a response AIRA also
    persists, in a column redaction cannot process (FRD-119 §5.4).

    The reasoning is put in a ``text`` field on purpose. A first version of this test used the
    vendor's own ``thinking`` field and **passed even when the type filter was removed**, because
    the field name differed too — so it proved nothing about the filter it was named after. The
    mutation harness caught that (`V4`). Selection must be by **block type**, and this is what
    holds it to that.
    """
    content = [
        {"type": "thinking", "text": "the user seems to be asking about their salary…"},
        {"type": "text", "text": "The answer is 42."},
    ]

    assert answer_text(content) == "The answer is 42."
    assert "salary" not in answer_text(content)


def test_an_unknown_block_type_is_dropped_rather_than_concatenated() -> None:
    """Fail closed on a block shape we have never seen: a vendor adding one must not start
    leaking its contents into answers the day it ships."""
    content = [
        {"type": "some_future_block", "text": "internal deliberation"},
        {"type": "text", "text": "hello"},
    ]

    assert answer_text(content) == "hello"


def test_the_response_maps_usage_and_the_stop_reason() -> None:
    response = anthropic_to_canonical(
        {
            "content": [{"type": "text", "text": "hi"}],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 10, "output_tokens": 3},
        },
        "claude-1",
    )

    assert response.text == "hi"
    assert response.finish_reason == "max_tokens", "truncation must be distinguishable from a stop"
    assert response.usage.prompt_tokens == 10
    assert response.usage.completion_tokens == 3


def test_cache_tokens_are_counted_as_input_rather_than_dropped() -> None:
    """They *were* input. Leaving them out would understate what the request cost."""
    usage = usage_of({"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 5})

    assert usage.prompt_tokens == 100


# -- streaming ---------------------------------------------------------------------------------


def test_usage_is_accumulated_across_two_events_not_replaced_by_the_last() -> None:
    """Anthropic sends the input count in `message_start` and the output count in `message_delta`,
    where Gemini puts everything in the last chunk. A last-event-wins implementation would report
    zero input tokens for every streamed Anthropic request."""
    assembler = StreamAssembler()
    assembler.feed({"type": "message_start", "message": {"usage": {"input_tokens": 40}}})
    assembler.feed(
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 7},
        }
    )
    final = assembler.feed({"type": "message_stop"})

    assert final is not None
    assert final.usage is not None
    assert final.usage.prompt_tokens == 40
    assert final.usage.completion_tokens == 7
    assert final.finish_reason == "stop"


def test_text_deltas_become_chunks_and_thinking_deltas_do_not() -> None:
    assembler = StreamAssembler()

    text = assembler.feed(
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hel"}}
    )
    thinking = assembler.feed(
        {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "hmm"}}
    )

    assert text is not None and text.text_delta == "hel"
    assert thinking is None, "the model's reasoning must not reach the client"


#: An agentic turn as Anthropic reports it: nearly the whole prompt read from the cache.
_CACHED_START = {
    "input_tokens": 100,
    "cache_read_input_tokens": 9000,
    "cache_creation_input_tokens": 500,
    "output_tokens": 1,
}


def _streamed(*events: dict[str, Any]) -> Any:
    """The chunk an assembler produces for the last of ``events``."""
    assembler = StreamAssembler()
    chunk = None
    for event in events:
        chunk = assembler.feed(event)
    return chunk


def test_a_streamed_answer_is_priced_exactly_as_the_same_answer_unstreamed() -> None:
    """**The workload the cache split exists for, streamed.** Agentic clients stream, and a turn
    whose prefix is 99 % cache reads was priced as if every token were fresh input — reads at the
    full rate instead of a tenth of it — because the streamed path kept only two of the counts."""
    final = _streamed(
        {"type": "message_start", "message": {"usage": _CACHED_START}},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 40},
        },
        {"type": "message_stop"},
    )

    assert final.usage == usage_of({**_CACHED_START, "output_tokens": 40})
    assert (final.usage.cached_input_tokens, final.usage.cache_write_tokens) == (9000, 500)
    assert final.usage.uncached_input_tokens == 100


def test_the_output_count_is_a_running_total_not_an_increment() -> None:
    """The vendor's documented stream: `message_start` says 1 output token so far, `message_delta`
    says 15 **in total**. Added up, every streamed answer billed one token it never produced."""
    final = _streamed(
        {"type": "message_start", "message": {"usage": {"input_tokens": 25, "output_tokens": 1}}},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 15},
        },
        {"type": "message_stop"},
    )

    assert final.usage.completion_tokens == 15


def test_an_input_count_repeated_at_the_end_is_not_counted_twice() -> None:
    """Current API versions repeat the input counts in `message_delta`; added to `message_start`'s,
    every streamed prompt was billed twice."""
    final = _streamed(
        {"type": "message_start", "message": {"usage": {"input_tokens": 100, "output_tokens": 1}}},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"input_tokens": 100, "output_tokens": 40},
        },
        {"type": "message_stop"},
    )

    assert (final.usage.prompt_tokens, final.usage.completion_tokens) == (100, 40)


def test_a_stream_that_breaks_off_has_already_said_what_it_cost() -> None:
    """The input is billed the moment the answer starts. A stream that ends before `message_stop`
    — the caller hung up, the provider failed — must still have handed that figure over, or the
    request settles as free."""
    text = _streamed(
        {"type": "message_start", "message": {"usage": _CACHED_START}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hel"}},
    )

    assert text.text_delta == "Hel"
    assert text.usage == usage_of(_CACHED_START)


def test_an_error_event_is_the_upstream_failing_not_the_end_of_the_answer() -> None:
    """`event: error` arrives inside a 200 — `overloaded_error` is the common one. Read as nothing,
    the stream just ended: no finish reason, no usage, the fallback never tried, and the audit row
    blaming the caller for leaving. It is raised with the status the same failure has as an
    answer's own, so it passes through and the chain moves on."""
    assembler = StreamAssembler()
    assembler.feed({"type": "message_start", "message": {"usage": {"input_tokens": 900}}})

    with pytest.raises(UpstreamError) as caught:
        assembler.feed(
            {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
        )

    assert caught.value.status_code == 503
    assert "overloaded_error" in caught.value.message


def test_argument_fragments_never_become_answer_text() -> None:
    """A tool call's arguments arrive as `input_json_delta`; outside a `tool_use` block they belong
    to nothing the caller asked for, and streamed as text they would read as the answer."""
    assert (
        StreamAssembler().feed(
            {
                "type": "content_block_delta",
                "delta": {"type": "input_json_delta", "partial_json": '{"path": '},
            }
        )
        is None
    )


@pytest.mark.parametrize(
    ("stop_reason", "expected"),
    [
        ("end_turn", FinishReason.STOP),
        ("stop_sequence", FinishReason.STOP),
        ("max_tokens", FinishReason.MAX_TOKENS),
        ("model_context_window_exceeded", FinishReason.MAX_TOKENS),
        ("tool_use", FinishReason.TOOL_USE),
        ("refusal", FinishReason.REFUSAL),
        ("pause_turn", FinishReason.OTHER),
        ("a_reason_added_next_year", FinishReason.OTHER),
        (None, FinishReason.STOP),
    ],
)
def test_every_stop_reason_has_one_canonical_word(
    stop_reason: str | None, expected: FinishReason
) -> None:
    """One vocabulary for every dialect (`FinishReason`). An unknown reason is `OTHER`, never
    `STOP`: a word nobody here knows is no evidence that the document is complete."""
    from aira_gateway.upstreams.vertex.anthropic_mapping import finish_reason

    assert finish_reason(stop_reason) is expected


# -- through the transport ----------------------------------------------------------------------


async def test_an_anthropic_request_carries_the_bearer_and_hits_raw_predict() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "hi"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    adapter = VertexAnthropicAdapter(
        _transport(handler), [VertexModel("eu", "anthropic", "claude-1")], default_max_tokens=2048
    )
    response = await adapter.generate(_request())

    assert response.text == "hi"
    assert seen["auth"] == "Bearer test-token"
    assert seen["url"].endswith("/publishers/anthropic/models/claude-1:rawPredict")
    assert seen["body"]["max_tokens"] == 2048, "max_tokens is required and must always be sent"


async def test_the_callers_own_output_bound_wins_over_the_default() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [], "usage": {}})

    adapter = VertexAnthropicAdapter(
        _transport(handler), [VertexModel("eu", "anthropic", "claude-1")], default_max_tokens=2048
    )
    await adapter.generate(_request(max_output_tokens=512))

    assert seen["body"]["max_tokens"] == 512


async def test_a_gemini_request_on_vertex_uses_generate_content() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
            },
        )

    adapter = VertexGeminiAdapter(_transport(handler), [VertexModel("eu", "google", "gemini-1")])
    response = await adapter.generate(_request("gemini-1"))

    assert response.text == "hi"
    assert seen["url"].endswith("/publishers/google/models/gemini-1:generateContent")


async def test_a_gemini_embedding_on_vertex_goes_to_predict_one_text_per_call() -> None:
    """Vertex does not serve `batchEmbedContents` and refuses `embedContent` for an API key; its
    embedding verb is `:predict`, which takes one text per call for `gemini-embedding-001`."""
    seen: list[tuple[str, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((str(request.url), body))
        text = body["instances"][0]["content"]
        embeddings = {"values": [float(len(text)), 1.0], "statistics": {"token_count": len(text)}}
        return httpx.Response(200, json={"predictions": [{"embeddings": embeddings}]})

    adapter = VertexGeminiAdapter(_transport(handler), [VertexModel("eu", "google", "embed-1")])
    vectors = await adapter.embed(
        CanonicalEmbeddingRequest(
            model="embed-1", texts=["a", "bbb"], task_type="RETRIEVAL_QUERY", dimensions=768
        )
    )

    assert vectors == [[1.0, 1.0], [3.0, 1.0]], "one vector per text, in the caller's order"
    # Where it was answered and what it cost, for the audit row's residency and its price.
    assert getattr(vectors, "served_region", None) == "eu"
    assert getattr(vectors, "input_tokens", None) == 4
    assert [url.rsplit(":", 1)[-1] for url, _ in seen] == ["predict", "predict"]
    assert seen[0][1] == {
        "instances": [{"content": "a", "task_type": "RETRIEVAL_QUERY"}],
        # Off, or an over-long text is embedded in part and answered with a 200.
        "parameters": {"autoTruncate": False, "outputDimensionality": 768},
    }


async def test_a_predict_answer_without_a_vector_is_the_providers_fault() -> None:
    adapter = VertexGeminiAdapter(
        _transport(lambda request: httpx.Response(200, json={"predictions": []})),
        [VertexModel("eu", "google", "embed-1")],
    )

    with pytest.raises(UpstreamError) as caught:
        await adapter.embed(CanonicalEmbeddingRequest(model="embed-1", texts=["a"]))

    assert caught.value.status_code == 502


async def test_an_upstream_status_is_preserved_so_the_route_can_pass_it_through() -> None:
    """429/503/504 mean something specific to a caller, and the route already maps them. A
    transport that flattened everything to 502 would lose that across every vendor at once."""
    adapter = VertexAnthropicAdapter(
        _transport(lambda request: httpx.Response(429, json={"error": "quota"})),
        [VertexModel("eu", "anthropic", "claude-1")],
        default_max_tokens=64,
    )

    with pytest.raises(UpstreamError) as caught:
        await adapter.generate(_request())

    assert caught.value.status_code == 429


async def test_anthropic_has_no_embedding_endpoint() -> None:
    adapter = VertexAnthropicAdapter(
        _transport(lambda request: httpx.Response(200)),
        [VertexModel("eu", "anthropic", "claude-1")],
        default_max_tokens=64,
    )

    with pytest.raises(UpstreamError):
        await adapter.embed(CanonicalEmbeddingRequest(model="claude-1", texts=["text"]))


# -- provenance ---------------------------------------------------------------------------------


def test_each_adapter_declares_where_its_models_run() -> None:
    """Residency is a configuration claim until something records it per request (FR-10)."""
    transport = _transport(lambda request: httpx.Response(200))
    gemini = VertexGeminiAdapter(transport, [VertexModel("europe-west1", "google", "gemini-1")])
    anthropic = VertexAnthropicAdapter(
        transport, [VertexModel("eu", "anthropic", "claude-1")], default_max_tokens=64
    )

    described = {m.name: m for m in [*gemini.models(), *anthropic.models()]}

    assert described["gemini-1"].provider == "vertex"
    assert described["gemini-1"].region == "europe-west1"
    assert described["gemini-1"].publisher == "google"
    assert described["claude-1"].region == "eu"
    assert described["claude-1"].publisher == "anthropic"


# -- the architecture assertion (FRD-115 §10) -----------------------------------------------


def test_no_code_above_the_adapters_knows_the_vendor() -> None:
    """`FRD-100` claims the canonical core is provider-agnostic. Until Anthropic, "two upstreams"
    meant two spellings of Google's format, so the claim had never been tested.

    This is the test. If a vendor name appears in *code* outside its own adapter package, the
    canonical core is vendor-shaped and the right response is to fix the core — not to smuggle a
    vendor field through it. Comments are allowed: explaining *why* a rule exists is exactly where
    a vendor should be named.
    """
    import ast
    from pathlib import Path

    source_root = Path(__file__).resolve().parents[1] / "src" / "aira_gateway"
    offenders: list[str] = []

    for path in source_root.rglob("*.py"):
        # The platform packages, and one deliberate exception: `residency.py` holds the region
        # allow-list, which names **every** cloud's regions on purpose (`ADR-0012` §6). "Which
        # regions may we use" is one policy question, and a list that could not name Azure's
        # would be the per-cloud list that decision rejected — and a per-cloud audit with it.
        exempt = ("upstreams/vertex", "upstreams/foundry", "residency.py")
        if any(part in path.as_posix() for part in exempt):
            continue
        tree = ast.parse(path.read_text())
        # Docstrings are documentation, not behaviour — stripped along with comments.
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                node.value.value = ""
        code = ast.unparse(tree).lower()
        # `azure` joins the list with `FRD-120`: the OpenAI *dialect* is deliberately
        # platform-free — that is what let Foundry reuse it unchanged — so the word belongs only in
        # the transport that reaches it. `openai` is **not** on this list, and the distinction is
        # the point: it names a wire format three platforms speak, not a vendor. The open models'
        # publishers and Google's endpoint for them join with `FRD-115` FR-2a, for the same reason.
        vendors = ("anthropic", "rawpredict", "claude", "azure", "deployments/")
        for vendor in (*vendors, "zai-org", "deepseek", "endpoints/openapi"):
            if vendor in code:
                offenders.append(f"{path.relative_to(source_root)}: {vendor}")

    assert not offenders, (
        "a vendor reached above its adapter — the canonical core is vendor-shaped:\n"
        + "\n".join(offenders)
    )


# == the second dialect's answers to the same two features (FRD-111, FRD-112) ====================


def test_thinking_becomes_anthropics_enabled_block() -> None:
    request = _request().model_copy(
        update={"thinking": Thinking(mode=ThinkingMode.LIMITED, tokens=2048)}
    )
    body = canonical_to_anthropic(request, max_tokens=8192)
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 2048}


def test_disabled_thinking_is_said_out_loud() -> None:
    """**Off is a value on the wire**, as on every other dialect. A model that thinks by default
    thinks whenever the parameter is absent — and bills for it — so a use case that switched
    thinking off would pay for thinking anyway. `budget_tokens: 0` is no way to say it either: that
    is an error rather than an instruction."""
    request = _request().model_copy(update={"thinking": Thinking(mode=ThinkingMode.DISABLED)})

    assert canonical_to_anthropic(request, max_tokens=8192)["thinking"] == {"type": "disabled"}


def test_no_thinking_setting_sends_no_thinking_block() -> None:
    """The other half: a request that resolved to *no* setting leaves the field out — it is the
    model's own default nobody asked to change (`thinking.resolve`)."""
    assert "thinking" not in canonical_to_anthropic(_request(), max_tokens=8192)


def test_a_budget_that_does_not_fit_the_output_allowance_is_refused_by_name() -> None:
    """FR-3a: Anthropic draws thinking tokens *from* `max_tokens`, so a budget at or above it
    describes a request that can never answer. The catalog cannot hold that combination; this is
    the backstop for a cap below what the declaration anticipated — the caller's own, or the
    adapter's default where neither the caller nor the catalogue named one.

    **A named refusal, not a crash.** It was a bare `ValueError`, which no surface catches as a
    refusal: a 500 with an `internal_error` row, and inside a chain it aborted the walk instead of
    trying the next model."""
    request = _request().model_copy(
        update={"thinking": Thinking(mode=ThinkingMode.LIMITED, tokens=4096)}
    )
    with pytest.raises(DialectUnsupported, match="does not fit") as caught:
        canonical_to_anthropic(request, max_tokens=4096)

    assert "4096" in str(caught.value)


async def test_a_chain_moves_on_from_a_claude_model_that_cannot_carry_the_budget() -> None:
    """The request a Claude candidate cannot carry may suit the next one: a Gemini model takes the
    same budget with no output allowance to draw it from. A refusal at mapping time skips the
    candidate by name, exactly as a condition refused before dispatch would."""
    from aira_gateway.pipeline.dispatch import dispatch_with_fallback
    from aira_gateway.upstreams.mock import MockProvider

    claude = VertexAnthropicAdapter(
        _transport(lambda request: httpx.Response(500)),
        [VertexModel("eu", "anthropic", "claude-1")],
        default_max_tokens=4096,
    )
    registry = ProviderRegistry([claude, MockProvider("backup-1")])
    request = _request(
        max_output_tokens=1000, thinking=Thinking(mode=ThinkingMode.LIMITED, tokens=2048)
    )

    dispatched = await dispatch_with_fallback(registry, request, ("backup-1",))

    assert dispatched.response.model == "backup-1"
    assert [entry.model for entry in dispatched.skipped] == ["claude-1"]
    assert "does not fit" in dispatched.skipped[0].reason


async def test_a_chain_of_one_that_cannot_carry_the_request_says_why_in_the_dialects_words() -> (
    None
):
    """With nobody to move on to, the dialect's own refusal is the answer: a request to change,
    which is what the caller can act on — not "no model could serve", which sends them looking for
    another model."""
    from aira_gateway.pipeline.dispatch import dispatch_with_fallback

    claude = VertexAnthropicAdapter(
        _transport(lambda request: httpx.Response(500)),
        [VertexModel("eu", "anthropic", "claude-1")],
        default_max_tokens=4096,
    )
    request = _request(
        max_output_tokens=1000, thinking=Thinking(mode=ThinkingMode.LIMITED, tokens=2048)
    )

    with pytest.raises(DialectUnsupported, match="does not fit"):
        await dispatch_with_fallback(ProviderRegistry([claude]), request, ())


def _continuing_a_call(**over: Any) -> CanonicalRequest:
    """The second turn of an agent loop: the model asked for a function, the caller ran it."""
    return CanonicalRequest(
        model="claude-1",
        messages=[
            CanonicalMessage(role=Role.USER, text="read hello.py"),
            CanonicalMessage(
                role=Role.MODEL,
                parts=[ToolCallPart(id="toolu_1", name="read_file", arguments={"path": "a"})],
            ),
            CanonicalMessage(
                role=Role.USER,
                parts=[ToolResultPart(call_id="toolu_1", name="read_file", content="print(1)")],
            ),
        ],
        **over,
    )


def test_thinking_cannot_continue_a_call_it_has_no_thinking_block_for() -> None:
    """Anthropic refuses a continued call without the model's own thinking block in front of it —
    *"Expected `thinking` or `redacted_thinking`, but found `tool_use`"* — and this gateway never
    hands that block to a caller, so it has none to send back. Refused here, by name and with the
    way out, rather than sent to collect the provider's 400; a chain skips to a model that can."""
    request = _continuing_a_call(thinking=Thinking(mode=ThinkingMode.LIMITED, tokens=2048))

    with pytest.raises(DialectUnsupported) as caught:
        canonical_to_anthropic(request, max_tokens=8192)

    assert "thinking disabled" in str(caught.value)


def test_a_call_continued_without_thinking_is_sent() -> None:
    """The way out the refusal names: the same turn with thinking off goes through."""
    body = canonical_to_anthropic(
        _continuing_a_call(thinking=Thinking(mode=ThinkingMode.DISABLED)), max_tokens=8192
    )

    assert body["thinking"] == {"type": "disabled"}
    assert body["messages"][1]["content"][0]["type"] == "tool_use"


def test_thinking_after_a_finished_tool_loop_is_sent() -> None:
    """Only the **latest** model turn is checked: once the model has answered in words, the calls
    before it are history, and thinking applies to the new question as usual."""
    request = _continuing_a_call(thinking=Thinking(mode=ThinkingMode.LIMITED, tokens=2048))
    request = request.model_copy(
        update={
            "messages": [
                *request.messages,
                CanonicalMessage(role=Role.MODEL, text="It prints 1."),
                CanonicalMessage(role=Role.USER, text="and now?"),
            ]
        }
    )

    assert canonical_to_anthropic(request, max_tokens=8192)["thinking"]["type"] == "enabled"


def test_a_schema_becomes_the_output_format() -> None:
    """A schema travels as `output_config.format`, a first-class parameter beside `tools`, so a
    schema and the caller's tools can coexist and no tool call is forced."""
    schema = parse_schema({"type": "OBJECT", "properties": {"a": {"type": "STRING"}}})
    body = canonical_to_anthropic(
        _request().model_copy(update={"response_schema": schema}), max_tokens=1024
    )

    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["format"]["schema"]["properties"]["a"]["type"] == "string"
    # The provider requires both of these on every object and rejects a schema without them; they
    # are filled in here rather than demanded of a caller whose schema is perfectly valid.
    assert body["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert body["output_config"]["format"]["schema"]["required"] == ["a"]
    assert "tool_choice" not in body


def test_the_document_is_read_back_out_of_the_text_block() -> None:
    """Where the forced tool used to put it in its input, the provider now returns the document as
    an ordinary text block — so reading it is the same as reading any other answer."""
    payload = {
        "content": [{"type": "text", "text": '{"a": "x"}'}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 2},
    }
    response = anthropic_to_canonical(payload, "claude-1", structured=True)

    assert json.loads(response.text) == {"a": "x"}
    assert response.finish_reason == "stop"


def test_a_schema_and_the_callers_tools_now_travel_together() -> None:
    """The exclusion was never our design: it existed only because one field had to serve two
    purposes. Both are separate parameters now, and the model may do either."""
    from aira_gateway.core.canonical import ToolDeclaration

    schema = parse_schema({"type": "OBJECT", "properties": {"a": {"type": "STRING"}}})
    body = canonical_to_anthropic(
        _request().model_copy(
            update={
                "response_schema": schema,
                "tools": (ToolDeclaration(name="read_file"),),
            }
        ),
        max_tokens=1024,
    )

    assert body["output_config"]["format"]["type"] == "json_schema"
    assert [tool["name"] for tool in body["tools"]] == ["read_file"]
    assert body["tools"][0]["strict"] is True
    # A function without parameters is still an object strict mode accepts: closed and qualified.
    assert body["tools"][0]["input_schema"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
        "required": [],
    }


def _tool_body(parameters: dict[str, Any]) -> dict[str, Any]:
    """The one tool declaration an Anthropic body carries for ``parameters``."""
    tool = ToolDeclaration(name="read_file", parameters=parse_schema(parameters))
    body = canonical_to_anthropic(_request(tools=(tool,)), max_tokens=1024)
    return body["tools"][0]


def test_a_strict_tool_schema_is_one_strict_mode_accepts() -> None:
    """**Strict tool use rejects an object that is not closed.** The tool was sent `strict` with
    the caller's schema as it came — Gemini-shaped, with no `additionalProperties` — so every
    request carrying it would have been refused by the provider. It is closed here, as the output
    schema always was, and the caller's own `required` is kept as written."""
    tool = _tool_body(
        {
            "type": "OBJECT",
            "properties": {
                "path": {"type": "STRING"},
                "range": {
                    "type": "OBJECT",
                    "properties": {"from": {"type": "INTEGER"}},
                    "required": ["from"],
                },
            },
            "required": ["path"],
        }
    )

    assert tool["strict"] is True
    schema = tool["input_schema"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["path"], "an optional argument stays optional"
    assert schema["properties"]["range"]["additionalProperties"] is False


@pytest.mark.parametrize(
    "parameters",
    [
        # A constraint strict mode does not support: tightened, it would be dropped.
        {
            "type": "OBJECT",
            "properties": {"p": {"type": "STRING", "pattern": "^x"}},
            "required": [],
        },
        # Says nothing about which arguments are required: strict would make every one mandatory.
        {"type": "OBJECT", "properties": {"path": {"type": "STRING"}}},
        # An object the caller left open: strict would close it.
        {
            "type": "OBJECT",
            "properties": {"meta": {"type": "OBJECT", "additionalProperties": True}},
            "required": ["meta"],
        },
    ],
)
def test_a_tool_strict_mode_would_change_is_sent_as_declared(parameters: dict[str, Any]) -> None:
    """**Never a strict schema the provider refuses, and never a changed tool.** The guarantee is
    this gateway's addition — the caller asked for none — so where buying it would drop a
    constraint, make an optional argument mandatory or close an open object, the tool goes out as
    the caller declared it, without `strict`."""
    tool = _tool_body(parameters)

    assert "strict" not in tool
    assert tool["input_schema"] == to_json_schema(parse_schema(parameters))


def test_a_schema_this_dialect_cannot_express_skips_the_candidate() -> None:
    """What survived the simplification, and it is real: this vocabulary is **narrower** than the
    one our surface accepts. A constraint sent and dropped produces an answer that satisfies the
    schema the caller sent and not the one they meant (`ADR-0012` §3)."""
    from aira_gateway.upstreams.vertex.anthropic_mapping import schema_refusal

    permissive = parse_schema({"type": "OBJECT", "properties": {"a": {"type": "STRING"}}})
    constrained = parse_schema(
        {"type": "OBJECT", "properties": {"a": {"type": "STRING", "pattern": "^x+$"}}}
    )

    assert schema_refusal(permissive) is None
    refusal = schema_refusal(constrained)
    assert refusal is not None and "pattern" in refusal


def test_a_model_that_answered_in_prose_did_not_satisfy_the_schema() -> None:
    """A real path with this mechanism, not a defensive one — and returning the prose as though it
    were the document is precisely what `FRD-112` FR-6 exists to prevent."""
    payload = {
        "content": [{"type": "text", "text": "Sure! Here is your JSON..."}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 2},
    }
    response = anthropic_to_canonical(payload, "claude-1", structured=True)

    assert response.text == ""
    assert response.finish_reason == SCHEMA_UNSATISFIED


def test_a_tool_call_instead_of_a_document_is_a_normal_turn_now() -> None:
    """**Inverted on 2026-08-08, deliberately.** Under the forced-tool mechanism a call to anything
    but our tool meant the schema went unsatisfied — there was nothing else it could mean, because
    the schema *was* the tool. With `output_config` a request can carry both, so a model calling a
    function instead of answering is an ordinary agent turn and reporting it as a failed schema
    would break the exchange."""
    payload = {
        "content": [{"type": "tool_use", "id": "c1", "name": "read_file", "input": {"a": "x"}}],
        "stop_reason": "tool_use",
        "usage": {},
    }
    response = anthropic_to_canonical(payload, "c", structured=True)

    assert response.finish_reason is FinishReason.TOOL_USE
    assert [call.name for call in response.tool_calls] == ["read_file"]
    # And the rule that decides it agrees: this mapper's verdict alone was asserted, while the
    # check every surface applies refused the same turn with a 502.
    from aira_gateway.api.serving import check_structured_result

    schema = parse_schema({"type": "OBJECT", "properties": {"a": {"type": "STRING"}}})
    check_structured_result(_request(response_schema=schema), response)


def test_prose_where_a_document_was_promised_is_still_refused() -> None:
    """The guarantee that had to survive the rewrite. Under the old mechanism "the model answered
    in prose" was visible for free — there was no tool call to read. Now prose and a document
    arrive through the same text block, so the text is parsed: a text that is not a document is
    not one, whatever the provider guarantees."""
    payload = {
        "content": [{"type": "text", "text": "Sure! Here is your JSON..."}],
        "stop_reason": "end_turn",
        "usage": {},
    }

    assert anthropic_to_canonical(payload, "c", structured=True).finish_reason == SCHEMA_UNSATISFIED


def test_an_ordinary_answer_is_unaffected_by_the_structured_path() -> None:
    payload = {
        "content": [{"type": "text", "text": "hello"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    assert anthropic_to_canonical(payload, "claude-1").text == "hello"


def test_a_model_name_cannot_escape_its_path_segment() -> None:
    """**The comment claimed an encoding the code did not do.**

    It built the segment with `httpx.URL(path=f"/{model}").path`, which leaves `/` and `..`
    untouched and *decodes* `%2f` — so `..%2f..%2fx` arrived as `../../x`, worse than the input.
    A model name that walks up the path reaches a different Google project, or a different API on
    the same host, with this deployment's service-account token attached.

    Two gates stand in front of it (`FRD-307`: only a catalogued, approved model dispatches) —
    which is the argument this project refuses everywhere else — and `AzureRoutes` had already
    solved the identical problem correctly one directory away.
    """
    url = _transport(lambda request: httpx.Response(200)).url(
        region="eu",
        publisher="google",
        model="../../evil",
        method="generateContent",
    )

    assert "/models/..%2F..%2Fevil:generateContent" in url
    assert "/../" not in url


def test_an_encoded_separator_is_not_decoded_into_one() -> None:
    """The nastier half: the old call *decoded* `%2f`, so a caller who wrote the escape got the
    separator. Encoding the percent is what stops one round of decoding from producing a path."""
    url = _transport(lambda request: httpx.Response(200)).url(
        region="eu", publisher="google", model="..%2f..%2fevil", method="generateContent"
    )

    assert "%252f" in url
    assert "/../" not in url


def test_a_vertex_refusal_carries_the_reason_it_gave() -> None:
    """One question, one answer — and it had two.

    The OpenAI dialect carried a provider's reason for a `400` and this transport did not, its
    comment reasoning that *"a Vertex error can quote the request"*. True of the response **body**
    and not of `error.message`, and the difference cost a diagnosis: a run of fifteen media types
    against a real Gemini model had fourteen answer correctly and the fifteenth come back as
    `Vertex upstream returned 400.`, while Vertex itself had said

        Unable to submit request because it has a mimeType parameter with value
        application/x-javascript, which is not supported.

    The whole answer, discarded one layer down. `FRD-129`: a `400` names a fault in the body *we*
    built, and its reason is the most actionable thing anybody gets.
    """
    answer = httpx.Response(
        400,
        json={"error": {"message": "mimeType application/x-javascript is not supported"}},
        request=httpx.Request("POST", "http://x/v1/generateContent"),
    )

    error = refused(answer, NAME)

    assert "application/x-javascript" in str(error)
    assert error.status_code == 400


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_only_a_400_carries_it(status: int) -> None:
    """A `401`/`403` is about *our* credentials — the caller cannot act on it and the message may
    name one. A `5xx` is the provider's internal noise. The same line the OpenAI dialect draws."""
    response = httpx.Response(
        status,
        json={"error": {"message": "service account key aira-prod@example is invalid"}},
        request=httpx.Request("POST", "http://x/v1/generateContent"),
    )

    error = refused(response, NAME)

    assert "aira-prod@example" not in str(error)
    assert error.status_code == status


# == cataloguing a Vertex model is enough to serve it ============================================


def _registry_with_vertex():
    from aira_gateway.config import GatewaySettings
    from aira_gateway.upstreams.base import ProviderRegistry
    from aira_gateway.upstreams.vertex import build_vertex_upstreams

    return ProviderRegistry(
        build_vertex_upstreams(
            GatewaySettings(
                vertex_project="p",
                vertex_api_key="AQ.key",
                vertex_models="",
                allowed_regions="europe-west1",
            )
        )
    )


def test_both_dialects_are_built_with_nothing_configured() -> None:
    """A deployment that has named no Vertex model still has the adapters.

    They used to be built only for what `AIRA_VERTEX_MODELS` listed, so on a fresh installation
    there was nothing to claim the provider — a Vertex model catalogued through the console was an
    entry that could never answer, and the console had to say so at the moment of declaring. An
    adapter with an empty configured list is the shape Google AI Studio's has had since stage B.
    """
    registry = _registry_with_vertex()

    assert "vertex" in registry.by_name()
    assert registry.models() == [], "nothing is configured, and that is the point"


def test_the_publisher_chooses_the_dialect() -> None:
    """Why `vertex` could not be claimed before: two adapters, one provider name.

    Vertex serves Google's models in the Gemini wire format and Anthropic's in theirs. The
    catalogue already carries the discriminator — `publisher` — and it is exactly the thing that
    decides the format, so routing on the pair costs nothing and makes the provider claimable.
    """
    registry = _registry_with_vertex()

    gemini = registry.provider_for("gemini-2.5-pro", "vertex", "google")
    claude = registry.provider_for("claude-sonnet-4-5", "vertex", "anthropic")

    assert type(gemini).__name__ == "VertexGeminiAdapter"
    assert type(claude).__name__ == "VertexAnthropicAdapter"
    assert gemini is not claude


def test_a_catalogued_model_is_addressed_from_its_declaration() -> None:
    """The region comes from the catalogue, which is the whole point.

    Vertex reaches a model at `<region>-aiplatform.googleapis.com`, so a model's *name* is not its
    address there. That second fact lived only in `AIRA_VERTEX_MODELS`, which is why cataloguing
    was not enough — and `addressing` was a column in both planes, carried over Kafka, that nothing
    read.
    """
    from aira_gateway.upstreams.vertex.regions import _targets

    # Both spellings, because a row written before a model could name several still carries the
    # singular one — and a redelivered Kafka event can carry it after a rollback.
    assert _targets({}, "google", "gemini-2.5-pro", {"region": "europe-west1"}) == (
        ("europe-west1", "google"),
    )
    assert _targets({}, "google", "gemini-2.5-pro", {"regions": ["europe-west1"]}) == (
        ("europe-west1", "google"),
    )


def test_a_configured_model_still_wins() -> None:
    """An installation that named a model in configuration keeps the region it named. Changing
    where existing traffic goes would be the worst possible way to add a feature."""
    from aira_gateway.upstreams.vertex.regions import VertexModel, _targets

    configured = {"m": VertexModel("europe-west4", "google", "m")}

    # One target and no chain: `AIRA_VERTEX_MODELS` has one entry per model by construction, so a
    # configured model has exactly one place and nothing to fall back to.
    assert _targets(configured, "google", "m", {"regions": ["europe-west1", "eu"]}) == (
        ("europe-west4", "google"),
    )


@pytest.mark.parametrize("asked", ["europe-west1", "europe-north1"])
async def test_a_probe_asks_the_region_it_reports_on(asked: str) -> None:
    """**A verdict per region is a claim about that region.** The reachability check of a model
    `AIRA_VERTEX_MODELS` pins to `europe-west4` answered "reachable" for every region it was asked
    about while contacting `europe-west4` each time — one region's answer under another's name. The
    probe addresses the region asked about, residency still checked, and nothing else."""
    contacted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        contacted.append(request.url.host)
        return httpx.Response(200, json={"totalTokens": 1})

    adapter = VertexGeminiAdapter(
        _transport(handler, allowed_regions=("europe-west1", "europe-west4", "europe-north1")),
        [VertexModel("europe-west4", "google", "gemini-2.5-pro")],
    )

    await adapter.ping("gemini-2.5-pro", {"regions": [asked]})

    assert contacted == [f"{asked}-aiplatform.googleapis.com"]


async def test_a_probe_without_a_region_asks_where_the_model_is_configured() -> None:
    contacted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        contacted.append(request.url.host)
        return httpx.Response(200, json={"totalTokens": 1})

    adapter = VertexGeminiAdapter(
        _transport(handler, allowed_regions=("europe-west4",)),
        [VertexModel("europe-west4", "google", "gemini-2.5-pro")],
    )

    await adapter.ping("gemini-2.5-pro")

    assert contacted == ["europe-west4-aiplatform.googleapis.com"]


def test_a_catalogued_model_with_no_region_is_refused_by_name() -> None:
    """Not guessed. A guess about residency is the one guess this product may not make, and the
    message says which of the two places to fix."""
    from aira_gateway.upstreams.base import AmbiguousModel
    from aira_gateway.upstreams.vertex.regions import _targets

    with pytest.raises(AmbiguousModel) as caught:
        _targets({}, "google", "gemini-2.5-pro", {})

    assert "region" in str(caught.value)


def test_the_global_endpoint_has_no_region_prefix() -> None:
    """**A host that resolves and 404s is worse than one that does not resolve.**

    `global` ships in `AIRA_ALLOWED_REGIONS` in the default deployment and `residency.py` treats
    it as a location, so a model could be catalogued there — and every request went to
    `global-aiplatform.googleapis.com`, which exists in DNS and answers `404`. That reads as *"the
    model is not available there"*, which is precisely the wrong conclusion. Reported as:

        *"a further problem is that I cannot call any 3.5 models to test them."*

    Measured on 2026-08-19: this installation's credential reaches `gemini-3.5-flash` and
    `gemini-3-flash-preview` **only** at `global` — 404 in `europe-west1`, `europe-west4`,
    `europe-north1` and `us-central1` alike. So the one region that serves the newest models was
    the one region the transport could not address.
    """
    from aira_gateway.upstreams.vertex.transport import host_for

    assert host_for("global") == "aiplatform.googleapis.com"
    # The other two shapes, so a fix to one is not a break of the others.
    assert host_for("eu") == "aiplatform.eu.rep.googleapis.com"
    assert host_for("europe-west1") == "europe-west1-aiplatform.googleapis.com"


# -- counting what a stream used when its caller left (FRD-128 FR-6) -------------------------


def _counted_by(seen: list[tuple[str, dict[str, Any]]], total: Any = 34):  # noqa: ANN202
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"totalTokens": total})

    return handler


async def test_a_count_goes_where_the_answer_came_from_and_sends_only_what_is_billed() -> None:
    """Asked in the region that answered — the text goes where it went — and with the parts of
    the body billed as input: sampling and limits add no token, and a count that included them
    would not be the vendor's figure for this request."""
    seen: list[tuple[str, dict[str, Any]]] = []
    adapter = VertexGeminiAdapter(_transport(_counted_by(seen)), [])
    request = _request(
        "gemini-x",
        addressing={"regions": ["europe-west1", "europe-west4"]},
        tools=(ToolDeclaration(name="get_weather"),),
        max_output_tokens=50,
        temperature=0.2,
    )

    total = await adapter.count_tokens(request, region="europe-west4")

    assert total == 34
    url, body = seen[0]
    assert "/locations/europe-west4/publishers/google/models/gemini-x:countTokens" in url
    assert set(body) == {"contents", "systemInstruction", "tools"}


async def test_a_count_with_no_region_known_goes_to_the_models_first_place() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []
    adapter = VertexGeminiAdapter(_transport(_counted_by(seen)), [])
    request = _request("gemini-x", addressing={"regions": ["europe-west1", "europe-west4"]})

    await adapter.count_tokens(request)

    assert "/locations/europe-west1/" in seen[0][0]


@pytest.mark.parametrize("total", [None, "34", -1, True])
async def test_a_count_without_a_usable_total_is_an_error_never_a_zero(total: Any) -> None:
    adapter = VertexGeminiAdapter(_transport(_counted_by([], total)), [])

    with pytest.raises(UpstreamError):
        await adapter.count_tokens(_request("gemini-x", addressing={"regions": ["europe-west1"]}))
