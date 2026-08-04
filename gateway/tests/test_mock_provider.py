from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role
from aira_gateway.upstreams.mock import MockProvider


def _request(text: str = "hello world", model: str = "mock-1") -> CanonicalRequest:
    return CanonicalRequest(model=model, messages=[CanonicalMessage(role=Role.USER, text=text)])


def test_models() -> None:
    model = MockProvider().models()[0]
    assert model.name == "mock-1"
    assert "generateContent" in model.supported_methods


async def test_generate_is_deterministic() -> None:
    provider = MockProvider()
    first = await provider.generate(_request())
    second = await provider.generate(_request())
    assert first == second
    assert first.text.startswith("[mock:mock-1]")
    assert first.usage.total_tokens == first.usage.prompt_tokens + first.usage.completion_tokens


async def test_generate_uses_last_user_message_and_counts_all_tokens() -> None:
    request = CanonicalRequest(
        model="mock-1",
        messages=[
            CanonicalMessage(role=Role.SYSTEM, text="sys"),
            CanonicalMessage(role=Role.USER, text="first"),
            CanonicalMessage(role=Role.MODEL, text="mid"),
            CanonicalMessage(role=Role.USER, text="latest question"),
        ],
    )
    response = await MockProvider().generate(request)
    assert "latest question" in response.text
    assert response.usage.prompt_tokens == 5


async def test_stream_generate_reconstructs_full_text() -> None:
    provider = MockProvider()
    request = _request("one two three four five six")
    chunks = [chunk async for chunk in provider.stream_generate(request)]

    assert len(chunks) >= 2
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage is not None

    streamed = "".join(chunk.text_delta for chunk in chunks).strip()
    assert streamed == (await provider.generate(request)).text


async def test_generate_respects_max_output_tokens() -> None:
    request = CanonicalRequest(
        model="mock-1",
        messages=[CanonicalMessage(role=Role.USER, text="hello")],
        max_output_tokens=2,
    )
    response = await MockProvider().generate(request)
    assert response.finish_reason == "max_tokens"
    assert response.usage.completion_tokens == 2
    assert len(response.text.split()) == 2


async def test_generate_no_truncation_when_limit_high() -> None:
    request = CanonicalRequest(
        model="mock-1",
        messages=[CanonicalMessage(role=Role.USER, text="hello")],
        max_output_tokens=1000,
    )
    assert (await MockProvider().generate(request)).finish_reason == "stop"


async def test_stream_propagates_max_tokens_finish_reason() -> None:
    request = CanonicalRequest(
        model="mock-1",
        messages=[CanonicalMessage(role=Role.USER, text="hello")],
        max_output_tokens=2,
    )
    chunks = [chunk async for chunk in MockProvider().stream_generate(request)]
    assert chunks[-1].finish_reason == "max_tokens"


async def test_embed_is_deterministic_and_sized() -> None:
    provider = MockProvider()
    assert await provider.embed("mock-1", "abc") == await provider.embed("mock-1", "abc")
    assert len(await provider.embed("mock-1", "abc")) == 8
