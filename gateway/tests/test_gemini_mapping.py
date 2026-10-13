from aira_gateway.api.gemini import schemas
from aira_gateway.api.gemini.mapping import (
    canonical_to_gemini,
    gemini_to_canonical,
    upstream_model_to_gemini,
)
from aira_gateway.core.canonical import CanonicalResponse, CanonicalUsage, Role
from aira_gateway.upstreams.base import UpstreamModel


def test_gemini_to_canonical_with_system_and_roles() -> None:
    request = schemas.GenerateContentRequest(
        contents=[
            schemas.Content(role="user", parts=[schemas.Part(text="hi")]),
            schemas.Content(role="model", parts=[schemas.Part(text="prev")]),
        ],
        systemInstruction=schemas.Content(parts=[schemas.Part(text="be nice")]),
        generationConfig=schemas.GenerationConfig(temperature=0.5, maxOutputTokens=64),
    )
    canonical = gemini_to_canonical("mock-1", request)

    assert canonical.model == "mock-1"
    assert [m.role for m in canonical.messages] == [Role.SYSTEM, Role.USER, Role.MODEL]
    assert canonical.temperature == 0.5
    assert canonical.max_output_tokens == 64


def test_gemini_to_canonical_defaults_role_to_user() -> None:
    request = schemas.GenerateContentRequest(
        contents=[schemas.Content(parts=[schemas.Part(text="x")])]
    )
    canonical = gemini_to_canonical("mock-1", request)
    assert canonical.messages[0].role == Role.USER
    assert canonical.temperature is None
    assert canonical.max_output_tokens is None


def test_canonical_to_gemini() -> None:
    response = CanonicalResponse(
        model="mock-1",
        text="hello",
        finish_reason="stop",
        usage=CanonicalUsage(prompt_tokens=2, completion_tokens=1),
    )
    gemini = canonical_to_gemini(response)
    assert gemini.candidates[0].content.parts[0].text == "hello"
    assert gemini.candidates[0].finishReason == "STOP"
    assert gemini.usageMetadata.totalTokenCount == 3
    assert gemini.modelVersion == "mock-1"


def test_upstream_model_to_gemini() -> None:
    gemini = upstream_model_to_gemini(UpstreamModel("mock-1", "mock-1", ("generateContent",)))
    assert gemini.name == "models/mock-1"
    assert gemini.displayName == "mock-1"
    assert gemini.supportedGenerationMethods == ["generateContent"]


def test_the_models_earlier_reasoning_is_not_replayed_as_something_it_said() -> None:
    """Where a use case enables reasoning, an answer carries its thoughts as parts marked
    `thought` (`FRD-135`), and the google-genai chat helper keeps the whole answer in the history
    of the next turn. Google does not read a thought part back as conversation; forwarded as plain
    text, the next model would be told it had *said* its private reasoning. So it is left out of
    the replayed conversation — Google's own reading — and the answer beside it is kept."""
    request = schemas.GenerateContentRequest.model_validate(
        {
            "contents": [
                {"role": "user", "parts": [{"text": "Wie spät ist es?"}]},
                {
                    "role": "model",
                    "parts": [
                        {"text": "INTERNAL REASONING: the clock says noon", "thought": True},
                        {"text": "Es ist 12 Uhr."},
                    ],
                },
                {"role": "user", "parts": [{"text": "Und in einer Stunde?"}]},
            ]
        }
    )

    canonical = gemini_to_canonical("gemini-2.5-pro", request)

    model_turn = canonical.messages[1]
    assert model_turn.role == Role.MODEL
    assert model_turn.text == "Es ist 12 Uhr."
    assert "INTERNAL REASONING" not in canonical.model_dump_json()
