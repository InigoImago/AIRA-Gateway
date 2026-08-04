from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role
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


def test_gemini_response_to_canonical_empty() -> None:
    response = gemini_response_to_canonical({}, "g")
    assert response.text == ""
    assert response.finish_reason == "stop"
    assert response.usage.total_tokens == 0


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
