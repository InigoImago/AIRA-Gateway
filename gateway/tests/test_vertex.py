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

from aira_common.tokens import StaticTokenSource
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role
from aira_gateway.upstreams.base import (
    AmbiguousModel,
    ProviderRegistry,
    UpstreamError,
    UpstreamModel,
)
from aira_gateway.upstreams.vertex import RegionNotAllowed, build_vertex_upstreams
from aira_gateway.upstreams.vertex.adapters import (
    VertexAnthropicAdapter,
    VertexGeminiAdapter,
    VertexModel,
)
from aira_gateway.upstreams.vertex.anthropic_mapping import (
    StreamAssembler,
    answer_text,
    anthropic_to_canonical,
    canonical_to_anthropic,
    usage_of,
)
from aira_gateway.upstreams.vertex.auth import CredentialsInvalid, ServiceAccount
from aira_gateway.upstreams.vertex.transport import VertexTransport, host_for


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
        vertex_credentials=json.dumps({"client_email": "a@b", "private_key": "x"}),
        vertex_models="eu/google/gemini-2.5-pro,eu/anthropic/claude-sonnet-4-5@20250929",
    )

    upstreams = build_vertex_upstreams(settings)

    assert len(upstreams) == 2, "one adapter per dialect"
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
    async def embed(self, model, text): ...  # noqa: ANN001, ANN201


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
        await adapter.embed("claude-1", "text")


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
        if "upstreams/vertex" in path.as_posix():
            continue
        tree = ast.parse(path.read_text())
        # Docstrings are documentation, not behaviour — stripped along with comments.
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                node.value.value = ""
        code = ast.unparse(tree).lower()
        for vendor in ("anthropic", "rawpredict", "claude"):
            if vendor in code:
                offenders.append(f"{path.relative_to(source_root)}: {vendor}")

    assert not offenders, (
        "a vendor reached above its adapter — the canonical core is vendor-shaped:\n"
        + "\n".join(offenders)
    )
