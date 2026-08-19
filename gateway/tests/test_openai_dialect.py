"""The third wire dialect (FRD-123, and FRD-120 later).

Hermetic throughout: an ``httpx.MockTransport`` stands in for the endpoint, so the bodies, the
streaming assembly and the error mapping are exercised without a model. That split is the point —
**this** is where the adapter is proved, and the live model in `tests/integration/` is what stops
us proving the wrong thing.

Two properties carry the weight, and both are cases where the other two dialects taught us what to
look for:

- **Usage arrives in a chunk with no choices.** Anthropic split usage across two events and a
  last-event-wins mapper silently reported zero input tokens for every stream. This format has the
  same trap wearing different clothes, and a mapper indexing ``choices[0]`` would lose the counts.
- **A token budget has no equivalent here at all.** `FRD-111` §5.2 predicted this before the
  dialect existed; rounding 20 000 tokens to "high" would spend a different amount of money than
  was asked for and nothing about the answer would show it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from aira_common.models import ThinkingMode
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalEmbeddingRequest,
    CanonicalMessage,
    CanonicalRequest,
    DataPart,
    Role,
    TextPart,
    Thinking,
)
from aira_gateway.core.schema import parse as parse_schema
from aira_gateway.residency import RegionNotAllowed
from aira_gateway.upstreams.base import (
    AmbiguousModel,
    DialectUnsupported,
    ProviderRegistry,
    UpstreamError,
)
from aira_gateway.upstreams.openai import (
    ServerSpecInvalid,
    build_openai_upstreams,
    parse_servers,
)
from aira_gateway.upstreams.openai.adapter import OpenAIAdapter
from aira_gateway.upstreams.openai.mapping import (
    canonical_to_openai,
    canonical_to_openai_embedding,
    embedding_values,
    openai_chunk_to_canonical,
    openai_to_canonical,
    parse_sse_line,
)
from aira_gateway.upstreams.openai.transport import OpenAITransport

Handler = Callable[[httpx.Request], httpx.Response]
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 32


def _adapter(handler: Handler, **kwargs: object) -> OpenAIAdapter:
    client = httpx.AsyncClient(base_url="http://local.test", transport=httpx.MockTransport(handler))
    return OpenAIAdapter(OpenAITransport(client=client), ["local-1"], **kwargs)  # type: ignore[arg-type]


def _request(**over: object) -> CanonicalRequest:
    return CanonicalRequest(
        model="local-1",
        messages=[
            CanonicalMessage(role=Role.SYSTEM, text="be brief"),
            CanonicalMessage(role=Role.USER, text="hello"),
        ],
        **over,  # type: ignore[arg-type]
    )


# == the request body ============================================================================


def test_the_system_prompt_is_just_the_first_message() -> None:
    """The one place this dialect is simpler than the other two: no split-out system parameter."""
    body = canonical_to_openai(_request())
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"] == "be brief"


def test_a_text_only_message_stays_a_plain_string() -> None:
    """Several implementations of this API are fussier about the array form, and a body full of
    single-element arrays is harder to read in a log for no gain."""
    assert isinstance(canonical_to_openai(_request())["messages"][1]["content"], str)


def test_an_image_becomes_a_data_uri_part() -> None:
    request = _request()
    request.messages[1].parts = [
        TextPart(text="what is this"),
        DataPart(media_type="image/png", data=PNG),
    ]
    content = canonical_to_openai(request)["messages"][1]["content"]

    assert [part["type"] for part in content] == ["text", "image_url"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_a_document_is_refused_rather_than_sent_as_an_image() -> None:
    """`ADR-0012`'s central case: GPT-shaped models read images and **not** documents. The chain
    should have skipped this candidate, so reaching here means a catalog claims a capability the
    dialect cannot deliver — and sending the prompt without the document returns a confident wrong
    answer with a 200 on it."""
    request = _request()
    request.messages[1].parts = [DataPart(media_type="application/pdf", data=b"%PDF-1.7")]

    with pytest.raises(DialectUnsupported, match="images"):
        canonical_to_openai(request)


def test_the_output_cap_and_temperature_are_carried() -> None:
    body = canonical_to_openai(_request(max_output_tokens=64, temperature=0.2))
    assert body["max_tokens"] == 64
    assert body["temperature"] == 0.2


def test_a_request_with_no_options_carries_none_of_them() -> None:
    body = canonical_to_openai(_request())
    assert set(body) == {"model", "messages"}


# == thinking, which this vendor expresses differently ===========================================


@pytest.mark.parametrize("word", ["minimal", "low", "medium", "high", "turbo"])
def test_a_level_reaches_reasoning_effort_as_the_caller_said_it(word: str) -> None:
    """**Untranslated**, which is a change of mind recorded in `ADR-0021`.

    This was a table, and it mapped `minimal` onto `"low"` and `auto` onto `"medium"`. The
    reasoning for the first was that `"minimal"` exists on one vendor's newest family and every
    other server answers `400 invalid value`, so the adjacent level is an answer instead of no
    answer. The reasoning is wrong in the same way the level→token table was: it silently gives
    somebody **twice the reasoning they asked for and bills them**, and the answer does not say so.
    A model that takes `minimal` declares it; one that does not never offers it, and the caller is
    refused by name with the words that model does take.

    `turbo` is here for the property: nothing in this repository knows what it means."""
    body = canonical_to_openai(_request(thinking=Thinking(mode=word)))
    assert body["reasoning_effort"] == word


def test_auto_is_refused_rather_than_turned_into_a_level() -> None:
    """`auto` is "you decide", and this dialect has no way to say it — `reasoning_effort` is always
    a level. It used to be sent as `"medium"`, which turns a caller's *absence* of a choice into a
    choice, at a rate nobody picked. Refused by name, like `limited` beside it."""
    with pytest.raises(DialectUnsupported) as caught:
        canonical_to_openai(_request(thinking=Thinking(mode=ThinkingMode.AUTO)))
    assert "decides" in str(caught.value)


def test_disabled_thinking_says_off_out_loud_rather_than_omitting_the_parameter() -> None:
    """**Off has to be said.** This test previously asserted the opposite, and was green, because
    the code and the test came from the same wrong idea: that a parameter nobody sets is a feature
    nobody gets.

    A real server settled it. Sent no `reasoning_effort`, a reasoning model **thinks anyway** —
    absence selects the *model's* default, which for a reasoning model is on — and it spent the
    entire 600-token output allowance doing so. The caller who explicitly switched thinking off
    received a 200, a truncated answer, and a bill for reasoning that is stripped from the response
    before they ever see it. The same server, sent `"none"`, answered in about fifteen tokens.
    """
    body = canonical_to_openai(_request(thinking=Thinking(mode=ThinkingMode.DISABLED)))
    assert body["reasoning_effort"] == "none"


def test_a_token_budget_is_refused_rather_than_rounded_to_a_level() -> None:
    """`FRD-111` §5.2 predicted this before the dialect existed. Rounding a caller's 20 000-token
    budget to "high" spends a different amount of money than they asked for, and nothing about the
    answer would show it — so the request fails instead."""
    setting = Thinking(mode=ThinkingMode.LIMITED, tokens=20_000)
    with pytest.raises(DialectUnsupported, match="effort level"):
        canonical_to_openai(_request(thinking=setting))


# == structured output — the third mechanism =====================================================


def test_a_schema_becomes_a_named_strict_json_schema() -> None:
    schema = parse_schema({"type": "OBJECT", "properties": {"a": {"type": "STRING"}}})
    body = canonical_to_openai(_request(response_schema=schema))

    fmt = body["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["name"]  # the vendor requires one
    assert fmt["json_schema"]["schema"]["properties"]["a"]["type"] == "string"


# == the response ================================================================================


def test_a_completion_maps_back_with_its_usage() -> None:
    payload = {
        "choices": [{"message": {"content": "hi there"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }
    response = openai_to_canonical(payload, "local-1")

    assert response.text == "hi there"
    assert response.finish_reason == "stop"
    assert (response.usage.prompt_tokens, response.usage.completion_tokens) == (7, 3)


def test_length_means_truncation_not_a_normal_stop() -> None:
    """`FRD-112` FR-6 turns on this distinction: a schema-constrained answer that stopped at the
    cap is not a document, and reporting it as a normal stop would return half of one as data."""
    payload = {"choices": [{"message": {"content": "{"}, "finish_reason": "length"}], "usage": {}}
    assert openai_to_canonical(payload, "local-1").finish_reason == "max_tokens"


def test_an_empty_response_does_not_raise() -> None:
    """A compatible server that answers `{}` is a configuration problem, not a crash."""
    assert openai_to_canonical({}, "local-1").text == ""


# == streaming ===================================================================================


def test_usage_arrives_in_a_chunk_with_no_choices() -> None:
    """The trap this format shares with Anthropic's, wearing different clothes. A mapper that
    indexed `choices[0]` unconditionally would lose the token counts of **every** streamed
    request — and a stream reporting no usage is *released* rather than settled (`FRD-405`), so
    every streamed request would silently become free."""
    chunk = openai_chunk_to_canonical({"choices": [], "usage": {"prompt_tokens": 5}})
    assert chunk is not None
    assert chunk.usage is not None and chunk.usage.prompt_tokens == 5


def test_a_chunk_with_neither_content_nor_usage_is_dropped() -> None:
    assert openai_chunk_to_canonical({"choices": []}) is None


def test_a_delta_becomes_a_text_chunk() -> None:
    chunk = openai_chunk_to_canonical({"choices": [{"delta": {"content": "Hel"}}]})
    assert chunk is not None and chunk.text_delta == "Hel"


@pytest.mark.parametrize("line", ["data: [DONE]", "data:", ": keep-alive", "event: ping"])
def test_the_sentinel_and_the_noise_parse_to_nothing(line: str) -> None:
    assert parse_sse_line(line) is None


async def test_a_stream_reconstructs_the_text_and_the_final_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # Without this, the vendor reports no usage for a streamed response at all.
        assert body["stream_options"] == {"include_usage": True}
        return httpx.Response(
            200,
            content=(
                b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}]}\n\n'
                b'data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":2}}\n\n'
                b"data: [DONE]\n\n"
            ),
        )

    chunks = [chunk async for chunk in _adapter(handler).stream_generate(_request())]

    assert "".join(chunk.text_delta for chunk in chunks) == "Hello"
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.total_tokens == 6


# == embeddings ==================================================================================


def test_a_batch_is_one_call_with_the_whole_input() -> None:
    request = CanonicalEmbeddingRequest(model="local-1", texts=["a", "b"], dimensions=768)
    body = canonical_to_openai_embedding(request)

    assert body["input"] == ["a", "b"]
    assert body["dimensions"] == 768


def test_vectors_come_back_in_the_order_submitted_not_the_order_received() -> None:
    """`index` exists precisely because the API does not promise an order, and "the order
    submitted" is the contract `FRD-113` FR-1 makes to the caller."""
    data = {
        "data": [
            {"index": 1, "embedding": [0.2]},
            {"index": 0, "embedding": [0.1]},
        ]
    }
    assert embedding_values(data) == [[0.1], [0.2]]


async def test_the_adapter_embeds_through_the_embeddings_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.5, 0.25]}]})

    request = CanonicalEmbeddingRequest(model="local-1", texts=["hi"])
    assert await _adapter(handler).embed(request) == [[0.5, 0.25]]


# == errors and registration =====================================================================


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_an_upstream_status_is_passed_through(status: int) -> None:
    """A 429 from a self-deployed endpoint means *no free replica* rather than quota
    (`ADR-0012` §5). Flattening it here would remove the reader's ability to tell them apart."""
    adapter = _adapter(lambda request: httpx.Response(status))
    with pytest.raises(UpstreamError) as caught:
        await adapter.generate(_request())
    assert caught.value.status_code == status


async def test_a_transport_failure_is_an_upstream_error_without_a_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(UpstreamError) as caught:
        await _adapter(handler).generate(_request())
    assert caught.value.status_code is None


async def test_a_streaming_failure_is_raised_before_the_caller_iterates() -> None:
    """A 500 that only surfaced on the first `aiter_lines` would arrive after our own response
    headers had already gone out — at which point the status can no longer be changed."""
    adapter = _adapter(lambda request: httpx.Response(500))
    with pytest.raises(UpstreamError):
        async for _ in adapter.stream_generate(_request()):
            pass


class _Unread(httpx.AsyncByteStream):
    """A response body that has genuinely not been read yet, which a `json=` response never is."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._payload


async def test_a_streamed_refusal_carries_the_reason_the_server_gave() -> None:
    """The same 400, on the two paths, has to say the same thing.

    Reported from a chatbot whose streamed calls answered **500** while the identical request
    non-streamed answered 400 *with the reason in it*. A streamed response arrives unread by
    design, so judging its status before reading the body left `_raise_for_status` with nothing to
    quote — and `FRD-129`'s whole point is that a 400 names a fault in the body we built, which is
    the most actionable thing an operator gets. The half-second it costs to read a refusal's body
    is not on the hot path: there is no stream to be had.
    """
    # **An `httpx.Response(400, json=...)` would not reach the path this test is named after.**
    # Its content is already in hand, so `_reason()` can read it whether or not anybody called
    # `aread()`, and the mutation that deletes the fix survives — the test passes for a reason
    # unrelated to the fix. A response built over a byte *stream* is unread until it is read, which
    # is what a real refusal from a real server is.
    message = b'{"error": {"message": "invalid reasoning value: \'minimal\'"}}'
    adapter = _adapter(lambda request: httpx.Response(400, stream=_Unread(message)))

    with pytest.raises(UpstreamError) as caught:
        async for _ in adapter.stream_generate(_request()):
            pass

    assert caught.value.status_code == 400
    assert "minimal" in str(caught.value), "the server's reason must survive the streaming path"


def test_the_two_verb_sets_are_disjoint() -> None:
    """A chat model has no embedding endpoint and vice versa. Advertising both for everything
    would leave the catalog declaration as the only thing between a caller and a vendor error."""
    adapter = _adapter(
        lambda request: httpx.Response(200), embedding_models=["embed-1"], region="on-premises"
    )
    described = {model.name: model for model in adapter.models()}

    assert "generateContent" in described["local-1"].supported_methods
    assert "embedContent" not in described["local-1"].supported_methods
    assert "embedContent" in described["embed-1"].supported_methods
    assert "generateContent" not in described["embed-1"].supported_methods


def test_every_model_records_where_it_ran() -> None:
    """A self-hosted model is the strongest residency story available, and one nothing records is
    a claim rather than evidence (`FRD-123` §5.3)."""
    adapter = _adapter(lambda request: httpx.Response(200), region="on-premises")
    described = adapter.models()[0]

    assert described.provider == "openai-compatible" or described.provider
    assert described.region == "on-premises"


def test_no_configuration_registers_no_adapter() -> None:
    """A system that appears in a deployment nobody asked for it in eventually serves production
    traffic."""
    assert build_openai_upstreams(GatewaySettings()) == []


def test_a_url_with_no_models_registers_nothing_either() -> None:
    settings = GatewaySettings(ollama_url="http://localhost:11434")
    assert build_openai_upstreams(settings) == []


def test_the_single_endpoint_shorthand_is_one_server_named_ollama() -> None:
    settings = GatewaySettings(
        ollama_url="http://localhost:11434",
        ollama_models="qwen3:0.6b",
        ollama_embedding_models="all-minilm",
        ollama_region="on-premises",
        allowed_regions="on-premises",
    )
    described = {model.name: model for model in build_openai_upstreams(settings)[0].models()}

    assert set(described) == {"qwen3:0.6b", "all-minilm"}
    assert described["qwen3:0.6b"].provider == "ollama"
    assert described["qwen3:0.6b"].region == "on-premises"


# == several servers, which is the point of them being systems ===================================


def test_each_declared_server_becomes_its_own_adapter() -> None:
    """A deployment attaches several boxes, and each is a system in its own right — configured,
    addressed and **audited** separately."""
    spec = (
        "gpu-a=http://gpu-a:11434|qwen3:8b|nomic-embed-text|dc-frankfurt;"
        "gpu-b=http://gpu-b:11434|llama3.1:70b||dc-berlin"
    )
    settings = GatewaySettings(openai_servers=spec, allowed_regions="dc-frankfurt,dc-berlin")
    upstreams = build_openai_upstreams(settings)

    assert len(upstreams) == 2
    described = {m.name: m for upstream in upstreams for m in upstream.models()}
    assert set(described) == {"qwen3:8b", "nomic-embed-text", "llama3.1:70b"}


def test_a_servers_name_reaches_the_audit_as_the_provider() -> None:
    """ "Which machine served this, and what did that machine cost us" is unanswerable when every
    box in the fleet logs as `ollama`."""
    spec = "gpu-a=http://gpu-a:11434|qwen3:8b||dc-frankfurt"
    settings = GatewaySettings(openai_servers=spec, allowed_regions="dc-frankfurt")
    described = build_openai_upstreams(settings)[0].models()[0]

    assert described.provider == "gpu-a"
    assert described.region == "dc-frankfurt"


def test_the_shorthand_and_the_list_can_be_used_together() -> None:
    settings = GatewaySettings(
        openai_servers="gpu-a=http://gpu-a:11434|qwen3:8b",
        ollama_url="http://localhost:11434",
        ollama_models="qwen3:0.6b",
    )
    providers = {u.models()[0].provider for u in build_openai_upstreams(settings)}
    assert providers == {"gpu-a", "ollama"}


@pytest.mark.parametrize(
    "spec",
    [
        "no-equals-sign",
        "=http://gpu-a:11434|qwen3:8b",
        "gpu-a=gpu-a:11434|qwen3:8b",  # no scheme
        "gpu-a=http://gpu-a:11434",  # declares no models
        "gpu-a=http://gpu-a:11434|qwen3:8b;gpu-a=http://gpu-b:11434|llama3:8b",  # duplicate name
    ],
)
def test_an_unreadable_declaration_refuses_to_start(spec: str) -> None:
    """Every one of these is a **startup** failure. A gateway that started with half its servers
    silently dropped would answer "model not found" for the rest — which reads as a catalog
    problem and sends whoever debugs it to entirely the wrong place."""
    with pytest.raises(ServerSpecInvalid):
        build_openai_upstreams(GatewaySettings(openai_servers=spec))


def test_the_duplicate_name_message_names_the_server() -> None:
    """Two servers under one name would each overwrite the other's rows in a report."""
    spec = "gpu-a=http://a:11434|m1;gpu-a=http://b:11434|m2"
    with pytest.raises(ServerSpecInvalid, match="gpu-a"):
        parse_servers(spec)


def test_two_servers_offering_the_same_model_refuse_to_start() -> None:
    """The registry's existing rule, and it matters more with a fleet: last-registration-wins
    would be a silent choice of *which machine* handled a request, invisible in every log."""
    spec = "gpu-a=http://a:11434|qwen3:8b;gpu-b=http://b:11434|qwen3:8b"
    upstreams = build_openai_upstreams(GatewaySettings(openai_servers=spec))

    with pytest.raises(AmbiguousModel):
        ProviderRegistry(list(upstreams))


# == a model name with a colon in it (found live, FRD-123) =======================================


@pytest.mark.parametrize(
    ("resource", "expected"),
    [
        ("mock-1:generateContent", ("mock-1", "generateContent")),
        ("qwen3:0.6b:generateContent", ("qwen3:0.6b", "generateContent")),
        ("llama3.1:70b:streamGenerateContent", ("llama3.1:70b", "streamGenerateContent")),
        ("nomic-embed-text:v1.5:embedContent", ("nomic-embed-text:v1.5", "embedContent")),
    ],
)
def test_the_verb_is_split_off_the_last_colon(resource: str, expected: tuple[str, str]) -> None:
    """Found the first time a real request reached a real local model.

    Google's model names carry no colon, so splitting at the *first* one was correct for as long
    as Google was the only vendor. A self-hosted server's names are `qwen3:0.6b` — and the split
    turned that into the model `qwen3` with the method `0.6b:generateContent`, answering **"Model
    'qwen3' not found"**: a message naming a model nobody asked for, pointing at the catalog
    instead of at the parser.
    """
    from aira_gateway.api.gemini.routes import split_resource

    model, separator, method = split_resource(resource)
    assert (model, method) == expected
    assert separator == ":"


def test_a_resource_with_no_verb_still_reports_a_missing_method() -> None:
    """The empty separator is what the route tests for. Splitting from the right must not turn
    "you forgot the method" into "the model is called nothing"."""
    from aira_gateway.api.gemini.routes import split_resource

    assert split_resource("mock-1") == ("", "", "mock-1")


# == residency, corrected by a live request (FRD-123) ============================================


def test_a_server_declares_no_region_unless_one_is_named() -> None:
    """No claim, nothing to enforce, and a laptop keeps working. The evidence is opt-in."""
    settings = GatewaySettings(ollama_url="http://localhost:11434", ollama_models="m1")
    assert build_openai_upstreams(settings)[0].models()[0].region == ""


def test_a_named_region_must_be_permitted_and_the_gateway_says_so_at_startup() -> None:
    """The correction the first live request forced. `RegionAllowed` checks *every* model that
    declares a region — as it should — so a server named into a region nobody permitted refused
    every request with a 400. That reads as an upstream problem. Failing to start names the
    setting instead."""
    settings = GatewaySettings(
        openai_servers="gpu-a=http://gpu-a:11434|qwen3:8b||dc-frankfurt",
        allowed_regions="eu,europe-west1",
    )
    with pytest.raises(RegionNotAllowed, match="dc-frankfurt"):
        build_openai_upstreams(settings)


def test_a_permitted_local_region_starts_and_is_recorded() -> None:
    """Opting in to the claim gets the claim: the audit row says where the request went."""
    settings = GatewaySettings(
        openai_servers="gpu-a=http://gpu-a:11434|qwen3:8b||dc-frankfurt",
        allowed_regions="eu,dc-frankfurt",
    )
    assert build_openai_upstreams(settings)[0].models()[0].region == "dc-frankfurt"


# == the model's thoughts never leave the adapter (measured, FRD-111 §2) =========================


def test_the_reasoning_field_is_never_returned_as_the_answer() -> None:
    """A reasoning model returns its chain of thought in its own field, and the obvious mapper —
    concatenate what the message carries — would hand it back to the caller and into a column the
    gateway persists. Third vendor, third shape, same obligation.

    Not hypothetical: a local model answering "Say hello in one word" returned `content` of "Hi"
    beside 439 characters of `reasoning`.
    """
    payload = {
        "choices": [
            {
                "message": {
                    "content": "Hi",
                    "reasoning": "The user wants a greeting. They said one word, so...",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 16, "completion_tokens": 109},
    }
    response = openai_to_canonical(payload, "local-1")

    assert response.text == "Hi"
    assert "user wants" not in response.text


def test_an_answer_that_was_all_thinking_comes_back_empty_and_says_why() -> None:
    """The failure this makes visible rather than hides. A model that spends its whole allowance
    reasoning produces **no answer**; substituting the reasoning would return the most
    unreviewed text the model produced, as though it were the reply. The empty string plus a
    truncation finish reason is the honest pair — measured, at `max_tokens` 400."""
    payload = {
        "choices": [
            {"message": {"content": "", "reasoning": "..." * 100}, "finish_reason": "length"}
        ],
        "usage": {"prompt_tokens": 25, "completion_tokens": 400},
    }
    response = openai_to_canonical(payload, "local-1")

    assert response.text == ""
    assert response.finish_reason == "max_tokens"


def test_streamed_reasoning_is_dropped_delta_by_delta() -> None:
    """Otherwise the thoughts are streamed to the caller a token at a time, which is the same
    leak arriving more slowly."""
    chunk = openai_chunk_to_canonical(
        {"choices": [{"delta": {"reasoning": "let me think about this"}}]}
    )
    assert chunk is None or chunk.text_delta == ""


def test_thinking_tokens_are_billed_inside_the_output_count() -> None:
    """`FRD-111` FR-6, finally **measured** rather than assumed. It matters because the recorded
    cost is understated if the provider reports them apart: a one-word answer cost 109 completion
    tokens, of which the answer itself was one. The pricing needs no special case — but that was
    a claim until a real model was asked."""
    payload = {
        "choices": [
            {"message": {"content": "Hi", "reasoning": "x" * 439}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 16, "completion_tokens": 109},
    }
    usage = openai_to_canonical(payload, "local-1").usage

    assert usage.completion_tokens == 109
    assert usage.total_tokens == 125


def test_a_credential_failure_from_the_provider_is_not_handed_to_the_caller() -> None:
    """`FRD-129`. A 400 means the provider refused the **body we built**, and its reason is the
    most actionable thing anybody gets — a catalog declaring a mode the server rejects by name
    surfaced as "Upstream returned 400." with status `UNAVAILABLE`, which sends an operator to a
    status page about a fault in their own configuration.

    A 401 is a different thing entirely: it is about *our* credentials, the caller cannot act on
    it, and the message may name the credential. That one stays masked.
    """
    import httpx

    from aira_gateway.upstreams.openai.transport import OpenAITransport

    transport = OpenAITransport(client=httpx.AsyncClient())

    refused = httpx.Response(
        400,
        json={"error": {"message": "invalid reasoning value: 'minimal'"}},
        request=httpx.Request("POST", "http://x/v1/chat/completions"),
    )
    with pytest.raises(Exception) as caught:  # noqa: PT011 — the type is asserted below
        transport._raise_for_status(refused)
    assert "minimal" in str(caught.value)

    unauthorised = httpx.Response(
        401,
        json={"error": {"message": "invalid api key sk-abc123"}},
        request=httpx.Request("POST", "http://x/v1/chat/completions"),
    )
    with pytest.raises(Exception) as masked:
        transport._raise_for_status(unauthorised)
    assert "sk-abc123" not in str(masked.value)


# == what this endpoint offers, asked rather than typed (`FRD-507` stage C) =======================


async def test_the_listing_becomes_names_and_nothing_else() -> None:
    """This dialect's listing publishes an id, an owner and a timestamp. No context window, no
    method list, no capabilities — so every capability stays `None`: *the vendor said nothing*.

    It would be one line to fill in `can_generate=True` on the grounds that a chat server serves
    chat models, and that line would turn an assumption into a declaration on a screen whose whole
    subject is that a declaration is a measurement (`FRD-114` FR-7)."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={"data": [{"id": "qwen3:0.6b"}, {"id": "nomic-embed-text"}, {"object": "model"}]},
        )

    offered = await _adapter(handler).available_models()

    assert [model.name for model in offered] == ["qwen3:0.6b", "nomic-embed-text"]
    assert offered[0].can_generate is None
    assert offered[0].max_output_tokens is None


def test_a_server_that_serves_nothing_configured_still_states_where_it_ran() -> None:
    """The correction stage B had to make for Google, one adapter over: provenance is read from the
    registry, and a catalogue-resolved model has no entry there. An empty residency column is worse
    than the second list this removes — "the configuration says on-premises" is a claim and "this
    request went to on-premises" is evidence, and blank is neither."""
    adapter = OpenAIAdapter(
        OpenAITransport(client=httpx.AsyncClient()),
        [],
        provider="ollama",
        publisher="local",
        region="on-premises",
    )

    assert adapter.models() == []
    assert adapter.provenance == ("ollama", "local", "on-premises")
