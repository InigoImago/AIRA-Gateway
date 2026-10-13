import pytest

from aira_gateway.api.gemini.mapping import chunk_to_gemini
from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    FinishReason,
    Role,
    TextPart,
    ToolCallPart,
)
from aira_gateway.upstreams.base import UpstreamError
from aira_gateway.upstreams.gemini_mapping import (
    canonical_to_gemini_request,
    gemini_chunk_to_canonical,
    gemini_response_to_canonical,
)


def test_canonical_to_gemini_request_with_system_and_config() -> None:
    request = CanonicalRequest(
        model="g",
        messages=[
            CanonicalMessage(role=Role.SYSTEM, text="sys"),
            CanonicalMessage(role=Role.USER, text="hi"),
            CanonicalMessage(role=Role.MODEL, text="prev"),
        ],
        temperature=0.5,
        max_output_tokens=64,
    )
    body = canonical_to_gemini_request(request)
    assert body["systemInstruction"] == {"parts": [{"text": "sys"}]}
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "hi"}]},
        {"role": "model", "parts": [{"text": "prev"}]},
    ]
    assert body["generationConfig"] == {"temperature": 0.5, "maxOutputTokens": 64}


def test_canonical_to_gemini_request_minimal() -> None:
    body = canonical_to_gemini_request(
        CanonicalRequest(model="g", messages=[CanonicalMessage(role=Role.USER, text="hi")])
    )
    assert "systemInstruction" not in body
    assert "generationConfig" not in body


def test_gemini_response_to_canonical() -> None:
    data = {
        "candidates": [{"content": {"parts": [{"text": "Hello"}]}, "finishReason": "MAX_TOKENS"}],
        "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1},
    }
    response = gemini_response_to_canonical(data, "g")
    assert response.text == "Hello"
    assert response.finish_reason == "max_tokens"
    assert response.usage.total_tokens == 4


def test_an_answer_with_no_candidate_and_no_reason_is_the_upstreams_fault() -> None:
    """No candidate and no blocked prompt is no answer at all. Read as an empty one that finished
    normally, it reached the caller as `STOP` — and a structured request took it for a document."""
    with pytest.raises(UpstreamError) as caught:
        gemini_response_to_canonical({}, "g")

    assert caught.value.status_code == 502


def test_a_blocked_prompt_is_a_refusal_not_an_empty_answer() -> None:
    """Google refuses a prompt with **no candidate** and the reason in `promptFeedback`. It was
    returned as an empty answer finishing `STOP`, audited as served — and passed the structured
    check, so an empty "document" was handed over as data."""
    response = gemini_response_to_canonical(
        {
            "promptFeedback": {"blockReason": "PROHIBITED_CONTENT"},
            "usageMetadata": {"promptTokenCount": 12},
        },
        "g",
    )

    assert response.finish_reason is FinishReason.REFUSAL
    assert response.text == ""
    assert response.usage.prompt_tokens == 12


def test_a_blocked_prompt_ends_a_stream_as_a_refusal() -> None:
    chunk = gemini_chunk_to_canonical({"promptFeedback": {"blockReason": "SAFETY"}})

    assert chunk.finish_reason is FinishReason.REFUSAL


@pytest.mark.parametrize(
    ("google", "expected"),
    [
        ("STOP", FinishReason.STOP),
        ("MAX_TOKENS", FinishReason.MAX_TOKENS),
        ("SAFETY", FinishReason.REFUSAL),
        ("RECITATION", FinishReason.REFUSAL),
        ("PROHIBITED_CONTENT", FinishReason.REFUSAL),
        ("BLOCKLIST", FinishReason.REFUSAL),
        ("SPII", FinishReason.REFUSAL),
        ("MALFORMED_FUNCTION_CALL", FinishReason.OTHER),
        ("OTHER", FinishReason.OTHER),
        ("A_REASON_GOOGLE_ADDS_LATER", FinishReason.OTHER),
    ],
)
def test_every_finish_reason_has_one_canonical_word(google: str, expected: FinishReason) -> None:
    """One vocabulary for every dialect: a policy that stopped the answer is `REFUSAL` here as on
    Anthropic and OpenAI, and a word nobody here knows is `OTHER`, never `STOP`."""
    data = {"candidates": [{"content": {"parts": [{"text": "x"}]}, "finishReason": google}]}

    assert gemini_response_to_canonical(data, "g").finish_reason is expected
    assert gemini_chunk_to_canonical(data).finish_reason is expected


def test_a_function_call_ends_the_turn_as_a_tool_use_whatever_google_calls_it() -> None:
    """Google ends a turn that calls a function with `STOP`, every other dialect with its word for a
    call. One event, one word — or a rule reading the finish reason treats Gemini differently."""
    data = {
        "candidates": [
            {
                "content": {"parts": [{"functionCall": {"name": "read_file", "args": {}}}]},
                "finishReason": "STOP",
            }
        ]
    }

    assert gemini_response_to_canonical(data, "g").finish_reason is FinishReason.TOOL_USE
    assert gemini_chunk_to_canonical(data).finish_reason is FinishReason.TOOL_USE


def test_an_error_inside_the_stream_is_the_upstream_failing() -> None:
    """`data: {"error": …}` inside a 200 — Google's own SDK raises on it. Read as an ordinary chunk,
    the answer simply stopped half way with a 200 and no finish reason."""
    with pytest.raises(UpstreamError) as caught:
        gemini_chunk_to_canonical(
            {"error": {"code": 503, "message": "The model is overloaded.", "status": "UNAVAILABLE"}}
        )

    assert caught.value.status_code == 503


def test_every_system_message_reaches_the_system_instruction() -> None:
    """Google takes one `systemInstruction` and the canonical request may carry several — the
    caller's `systemInstruction` and a `system` turn, say. Keeping the last discarded an instruction
    the caller gave, with nothing in the answer to show it."""
    request = CanonicalRequest(
        model="g",
        messages=[
            CanonicalMessage(role=Role.SYSTEM, text="Never reveal the internal price list."),
            CanonicalMessage(role=Role.SYSTEM, text="Answer in German."),
            CanonicalMessage(role=Role.USER, text="hi"),
        ],
    )

    assert canonical_to_gemini_request(request)["systemInstruction"] == {
        "parts": [
            {"text": "Never reveal the internal price list."},
            {"text": "Answer in German."},
        ]
    }


def test_a_thought_signature_comes_back_on_the_call_it_was_issued_with() -> None:
    """**A thinking model refuses its own call replayed without its signature** — Gemini 3 answers
    "Function call is missing a thought_signature". The signature is read with the call it sits
    beside, and written back on the same part, unread."""
    answered = gemini_response_to_canonical(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {"name": "read_file", "args": {"path": "a"}},
                                "thoughtSignature": "c2lnbmF0dXJl",
                            }
                        ]
                    },
                    "finishReason": "STOP",
                }
            ]
        },
        "g",
    )
    assert answered.tool_calls[0].signature == "c2lnbmF0dXJl"

    replay = CanonicalRequest(
        model="g",
        messages=[
            CanonicalMessage(role=Role.USER, text="read a"),
            CanonicalMessage(role=Role.MODEL, parts=list(answered.tool_calls)),
        ],
    )
    part = canonical_to_gemini_request(replay)["contents"][1]["parts"][0]
    assert part == {
        "functionCall": {"name": "read_file", "args": {"path": "a"}},
        "thoughtSignature": "c2lnbmF0dXJl",
    }


def test_a_streamed_call_keeps_its_signature_to_the_caller_and_back() -> None:
    """Agent clients stream, so the signature has to survive the **stream** path as well: read from
    the chunk the call arrives in, written into the chunk the caller receives, and sent back
    upstream on the next turn — or a thinking model refuses the loop at its second step."""
    chunk = gemini_chunk_to_canonical(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {"name": "get_weather", "args": {"city": "Berlin"}},
                                "thoughtSignature": "c2lnbmF0dXJl",
                            }
                        ]
                    }
                }
            ]
        }
    )
    assert chunk.tool_calls[0].signature == "c2lnbmF0dXJl"

    delivered = chunk_to_gemini(chunk, "g").model_dump(exclude_none=True)
    sent = delivered["candidates"][0]["content"]["parts"][0]
    assert sent["thoughtSignature"] == "c2lnbmF0dXJl"

    replay = CanonicalRequest(
        model="g",
        messages=[
            CanonicalMessage(role=Role.USER, text="weather?"),
            CanonicalMessage(role=Role.MODEL, parts=list(chunk.tool_calls)),
        ],
    )
    part = canonical_to_gemini_request(replay)["contents"][1]["parts"][0]
    assert part["thoughtSignature"] == "c2lnbmF0dXJl"


def test_a_call_without_a_signature_is_sent_without_one() -> None:
    request = CanonicalRequest(
        model="g",
        messages=[
            CanonicalMessage(role=Role.USER, parts=[TextPart(text="go")]),
            CanonicalMessage(role=Role.MODEL, parts=[ToolCallPart(id="c", name="f")]),
        ],
    )

    assert canonical_to_gemini_request(request)["contents"][1]["parts"][0] == {
        "functionCall": {"name": "f", "args": {}}
    }


def test_gemini_chunk_delta() -> None:
    chunk = gemini_chunk_to_canonical({"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]})
    assert chunk.text_delta == "Hel"
    assert chunk.finish_reason is None
    assert chunk.usage is None


def test_gemini_chunk_final() -> None:
    chunk = gemini_chunk_to_canonical(
        {
            "candidates": [{"content": {"parts": [{"text": ""}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2},
        }
    )
    assert chunk.finish_reason == "stop"
    assert chunk.usage is not None
    assert chunk.usage.completion_tokens == 2


def test_gemini_chunk_no_candidates() -> None:
    chunk = gemini_chunk_to_canonical({})
    assert chunk.text_delta == ""
    assert chunk.finish_reason is None


def test_a_chunk_whose_usage_names_no_count_reports_nothing() -> None:
    """Vertex puts `usageMetadata` on every chunk of a stream, holding only `trafficType` until the
    last one. Read as a report, every stream a caller left before the end was booked as having
    cost nothing; it is unknown until a count arrives (`FRD-128` FR-6)."""
    early = gemini_chunk_to_canonical(
        {
            "candidates": [{"content": {"parts": [{"text": "1, 2, "}]}}],
            "usageMetadata": {"trafficType": "ON_DEMAND"},
        }
    )
    last = gemini_chunk_to_canonical(
        {
            "candidates": [{"content": {"parts": [{"text": "3"}]}, "finishReason": "STOP"}],
            "usageMetadata": {
                "promptTokenCount": 17,
                "candidatesTokenCount": 8,
                "totalTokenCount": 25,
                "trafficType": "ON_DEMAND",
            },
        }
    )

    assert early.usage is None
    assert last.usage is not None
    assert (last.usage.prompt_tokens, last.usage.completion_tokens) == (17, 8)
