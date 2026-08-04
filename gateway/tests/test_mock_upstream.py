from aira_gateway.upstreams.mock import MockCompletion, MockUpstream


def test_complete_is_deterministic() -> None:
    upstream = MockUpstream()
    first = upstream.complete("Hello world")
    second = upstream.complete("Hello world")
    assert first == second
    assert isinstance(first, MockCompletion)


def test_complete_content_and_tokens() -> None:
    completion = MockUpstream(model="mock-x").complete("one two three")
    assert completion.model == "mock-x"
    assert "[mock:mock-x]" in completion.content
    assert "one two three" in completion.content
    assert completion.prompt_tokens == 3
    assert completion.completion_tokens == len(completion.content.split())


def test_complete_truncates_and_flattens_prompt() -> None:
    completion = MockUpstream().complete("line1\nline2 " + "x" * 200)
    assert "\n" not in completion.content


def test_embed_is_deterministic_and_sized() -> None:
    upstream = MockUpstream()
    vec1 = upstream.embed("some text", dimensions=8)
    vec2 = upstream.embed("some text", dimensions=8)
    assert vec1 == vec2
    assert len(vec1) == 8
    assert all(0.0 <= v < 1.0 for v in vec1)


def test_embed_respects_dimensions() -> None:
    assert len(MockUpstream().embed("abc", dimensions=4)) == 4
