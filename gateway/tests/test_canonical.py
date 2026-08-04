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
