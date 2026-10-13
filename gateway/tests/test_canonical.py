from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalUsage,
    Role,
)


def test_total_tokens_is_computed() -> None:
    usage = CanonicalUsage(prompt_tokens=3, completion_tokens=4)
    assert usage.total_tokens == 7
    assert usage.model_dump()["total_tokens"] == 7


def test_last_user_text_returns_last_user_message() -> None:
    request = CanonicalRequest(
        model="m",
        messages=[
            CanonicalMessage(role=Role.USER, text="a"),
            CanonicalMessage(role=Role.MODEL, text="b"),
        ],
    )
    assert request.last_user_text() == "a"


def test_last_user_text_falls_back_to_last_message() -> None:
    request = CanonicalRequest(model="m", messages=[CanonicalMessage(role=Role.MODEL, text="only")])
    assert request.last_user_text() == "only"


def test_last_user_text_empty_when_no_messages() -> None:
    assert CanonicalRequest(model="m", messages=[]).last_user_text() == ""


def test_a_finish_reason_is_one_word_of_one_vocabulary() -> None:
    """Every rule that reads how an answer ended reads one of `FinishReason`'s words. A vendor's own
    word reaching past its mapper — `safety`, `content_filter` — is refused here rather than
    carried to a rule that has never heard of it."""
    import pydantic
    import pytest

    from aira_gateway.core.canonical import CanonicalChunk, CanonicalResponse, FinishReason

    usage = CanonicalUsage(prompt_tokens=1, completion_tokens=1)

    assert CanonicalResponse(
        model="m", text="", usage=usage, finish_reason="tool_use"
    ).finish_reason is (FinishReason.TOOL_USE)
    assert CanonicalChunk(text_delta="").finish_reason is None
    with pytest.raises(pydantic.ValidationError):
        CanonicalResponse(model="m", text="", usage=usage, finish_reason="content_filter")


def test_a_chunk_says_nothing_about_where_it_was_served_unless_told() -> None:
    """``None`` on every chunk but the first a regional dialect stamps: absent is not a region."""
    from aira_gateway.core.canonical import CanonicalChunk

    assert CanonicalChunk(text_delta="x").served_region is None
