"""Speech output on the Gemini surface (`FRD-624`, `ADR-0026`).

A spoken answer is an ordinary model call: text in, one `inlineData` part of PCM out, billed as
output tokens. The gateway carries it where the provider can serve it, and refuses by name where
the provider would answer an unexplained 400. Only a model that speaks serves it. The audit trail
keeps its description and hash, never the audio.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from aira_common.models import Capability
from aira_gateway.api.gemini import schemas
from aira_gateway.api.gemini.mapping import (
    canonical_to_gemini,
    chunk_to_gemini,
    gemini_to_canonical,
)
from aira_gateway.attachments import strip_attachments
from aira_gateway.audio import AudioDigest, duration
from aira_gateway.catalog import ModelDeclaration
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    DataPart,
    Role,
    SpeakerVoice,
    Speech,
)
from aira_gateway.requirements import SpeechSupported
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel
from aira_gateway.upstreams.gemini_mapping import (
    canonical_to_gemini_request,
    gemini_chunk_to_canonical,
    gemini_response_to_canonical,
)
from aira_gateway.upstreams.mock import SPEECH_MEDIA_TYPE, MockProvider

PCM = "audio/L16;codec=pcm;rate=24000"
VOICE = {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}}
TWO_SPEAKERS = {
    "multiSpeakerVoiceConfig": {
        "speakerVoiceConfigs": [
            {"speaker": "Anna", "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}},
            {"speaker": "Ben", "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Puck"}}},
        ]
    }
}
USAGE = CanonicalUsage(prompt_tokens=2, completion_tokens=22)


def _request(config: dict, **extra: object) -> schemas.GenerateContentRequest:
    return schemas.GenerateContentRequest.model_validate(
        {
            "contents": [{"role": "user", "parts": [{"text": "Guten Morgen."}]}],
            "generationConfig": config,
            **extra,
        }
    )


def _spoken(**speech: object) -> dict:
    return {"responseModalities": ["AUDIO"], "speechConfig": speech or VOICE}


def _canonical(text: str = "Guten Morgen.", **speech: object) -> CanonicalRequest:
    return CanonicalRequest(
        model="tts",
        messages=[CanonicalMessage(role=Role.USER, text=text)],
        speech=Speech(**speech) if speech else Speech(voice="Kore"),
    )


# == the surface: carried, and refused by name ===================================================


def test_one_voice_is_carried_to_the_canonical_request() -> None:
    assert gemini_to_canonical("tts", _request(_spoken())).speech == Speech(voice="Kore")


def test_the_sdk_spelling_inside_speech_config_is_accepted() -> None:
    """Captured from `google-genai` 2.17: inside `speechConfig` it writes snake_case, as it does
    inside `thinkingConfig`. Refusing it would make every SDK speech call a 400."""
    sdk = {
        "voice_config": {"prebuilt_voice_config": {"voice_name": "Kore"}},
        "language_code": "de-DE",
    }
    canonical = gemini_to_canonical(
        "tts", _request({"responseModalities": ["AUDIO"], "speechConfig": sdk})
    )
    assert canonical.speech == Speech(voice="Kore", language="de-DE")


def test_several_speakers_are_carried_in_order() -> None:
    canonical = gemini_to_canonical("tts", _request(_spoken(**TWO_SPEAKERS)))
    assert canonical.speech == Speech(
        speakers=(
            SpeakerVoice(speaker="Anna", voice="Kore"),
            SpeakerVoice(speaker="Ben", voice="Puck"),
        )
    )


def test_a_text_request_carries_no_speech() -> None:
    assert gemini_to_canonical("m", _request({"responseModalities": ["TEXT"]})).speech is None


@pytest.mark.parametrize(
    ("config", "extra", "named"),
    [
        pytest.param(
            {"responseModalities": ["AUDIO"]}, {}, "speechConfig", id="audio-without-voice"
        ),
        pytest.param({"speechConfig": VOICE}, {}, "responseModalities", id="voice-without-audio"),
        pytest.param(
            {"responseModalities": ["TEXT", "AUDIO"], "speechConfig": VOICE},
            {},
            "speech alone",
            id="text-and-audio",
        ),
        pytest.param({"responseModalities": ["IMAGE"]}, {}, "responseModalities", id="image"),
        pytest.param(
            _spoken(),
            {"systemInstruction": {"parts": [{"text": "Sprich freundlich."}]}},
            "systemInstruction",
            id="system-instruction",
        ),
        pytest.param(
            {**_spoken(), "thinkingConfig": {"includeThoughts": True}},
            {},
            "includeThoughts",
            id="thoughts",
        ),
        pytest.param(
            {
                **_spoken(),
                "responseMimeType": "application/json",
                "responseSchema": {"type": "OBJECT"},
            },
            {},
            "responseSchema",
            id="schema",
        ),
        pytest.param(_spoken(**VOICE, **TWO_SPEAKERS), {}, "exactly one", id="both-voice-forms"),
        pytest.param(
            _spoken(voiceConfig={"prebuiltVoiceConfig": {"voiceName": "../Kore"}}),
            {},
            "voice name",
            id="voice-name",
        ),
        pytest.param(
            _spoken(**VOICE, languageCode="deutsch bitte"), {}, "language code", id="lang"
        ),
        pytest.param(
            _spoken(
                multiSpeakerVoiceConfig={
                    "speakerVoiceConfigs": [
                        {"speaker": "Anna", "voiceConfig": VOICE["voiceConfig"]},
                        {"speaker": "Anna", "voiceConfig": VOICE["voiceConfig"]},
                    ]
                }
            ),
            {},
            "twice",
            id="same-speaker-twice",
        ),
    ],
)
def test_what_the_provider_would_refuse_without_a_reason_is_refused_by_name(
    config: dict, extra: dict, named: str
) -> None:
    """Measured on Vertex: a voice without the modality, the modality without a voice, and a
    `systemInstruction` are each an unexplained `400`, and `includeThoughts` fails every speech
    request. The caller is told what to change instead."""
    with pytest.raises(ValidationError) as caught:
        _request(config, **extra)
    assert named in str(caught.value)


# == the surface's answer ========================================================================


def test_the_answer_is_one_inline_data_part_as_google_sends_it() -> None:
    audio = DataPart(media_type=PCM, data=b"\x01\x02\x03\x04")
    response = CanonicalResponse(model="tts", text="", audio=audio, usage=USAGE)

    body = canonical_to_gemini(response).model_dump(exclude_none=True)

    assert body["candidates"][0]["content"]["parts"] == [
        {"inlineData": {"mimeType": PCM, "data": base64.b64encode(audio.data).decode()}}
    ]


def test_a_streamed_piece_of_speech_is_one_inline_data_part() -> None:
    chunk = CanonicalChunk(text_delta="", audio_delta=DataPart(media_type=PCM, data=b"\x00\x01"))

    parts = chunk_to_gemini(chunk, "tts").model_dump(exclude_none=True)["candidates"][0]["content"]

    assert parts["parts"] == [{"inlineData": {"mimeType": PCM, "data": "AAE="}}]


# == the Gemini dialect ==========================================================================


def test_the_provider_is_asked_for_audio_in_its_own_words() -> None:
    request = _canonical(voice="Kore", language="de-DE").model_copy(
        update={"include_reasoning": True}
    )

    config = canonical_to_gemini_request(request)["generationConfig"]

    assert config["responseModalities"] == ["AUDIO"]
    assert config["speechConfig"] == {
        "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}},
        "languageCode": "de-DE",
    }
    # Measured: `includeThoughts` on a speech model is a 400 (`FRD-135` FR-5).
    assert "thinkingConfig" not in config


def test_several_speakers_reach_the_provider() -> None:
    request = _canonical(
        speakers=(
            SpeakerVoice(speaker="Anna", voice="Kore"),
            SpeakerVoice(speaker="Ben", voice="Puck"),
        )
    )

    config = canonical_to_gemini_request(request)["generationConfig"]

    assert config["speechConfig"] == {
        "multiSpeakerVoiceConfig": {
            "speakerVoiceConfigs": [
                {"speaker": "Anna", "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}},
                {"speaker": "Ben", "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Puck"}}},
            ]
        }
    }


def test_the_audio_google_returns_is_read_back_and_billed_as_output() -> None:
    """The shape Vertex returned for `gemini-2.5-flash-tts` in europe-west1."""
    pcm = b"\x10\x20" * 4
    data = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {"inlineData": {"mimeType": PCM, "data": base64.b64encode(pcm).decode()}}
                    ],
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 2,
            "candidatesTokenCount": 22,
            "totalTokenCount": 24,
            "candidatesTokensDetails": [{"modality": "AUDIO", "tokenCount": 22}],
        },
    }

    answer = gemini_response_to_canonical(data, "gemini-2.5-flash-tts")

    assert answer.audio == DataPart(media_type=PCM, data=pcm)
    assert answer.text == ""
    assert answer.usage.completion_tokens == 22


def test_a_stream_is_read_as_pieces_of_audio_and_a_closing_chunk() -> None:
    """Vertex streamed a paragraph as 286 chunks of audio. The last carries an empty text part,
    `STOP` and the usage."""
    piece = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"inlineData": {"mimeType": PCM, "data": "AAE="}}],
                }
            }
        ]
    }
    closing = {
        "candidates": [
            {"content": {"role": "model", "parts": [{"text": ""}]}, "finishReason": "STOP"}
        ],
        "usageMetadata": {
            "promptTokenCount": 34,
            "candidatesTokenCount": 284,
            "totalTokenCount": 318,
        },
    }

    first = gemini_chunk_to_canonical(piece)
    last = gemini_chunk_to_canonical(closing)

    assert first.audio_delta == DataPart(media_type=PCM, data=b"\x00\x01")
    assert last.audio_delta is None
    assert last.finish_reason == "stop"
    assert last.usage is not None and last.usage.completion_tokens == 284


# == who may serve it ============================================================================


class _Catalog:
    def __init__(self, *capabilities: Capability) -> None:
        self._declaration = ModelDeclaration(
            name="m", declared=True, in_catalog=True, capabilities=frozenset(capabilities)
        )

    async def declaration(self, model: str) -> ModelDeclaration:
        return replace(self._declaration, name=model)


class _Silent:
    """A dialect with no field for a voice."""

    speaks = False

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("m", "m", ("generateContent",))]


async def test_only_a_model_that_speaks_on_a_dialect_that_can_ask_serves_speech() -> None:
    speaking = ProviderRegistry([MockProvider("m")])
    silent = ProviderRegistry([_Silent()])

    assert await SpeechSupported(speaking, _Catalog(Capability.SPEECH)).refusal("m") is None
    no_speech = await SpeechSupported(speaking, _Catalog(Capability.GENERATE)).refusal("m")
    no_words = await SpeechSupported(silent, _Catalog(Capability.SPEECH)).refusal("m")

    assert no_speech is not None and "speech" in no_speech
    assert no_words is not None and "dialect" in no_words


# == what the audit trail keeps ==================================================================


def test_the_description_names_what_was_served_without_keeping_it() -> None:
    pcm = bytes(range(256)) * 188
    digest = AudioDigest()
    digest.add(DataPart(media_type=PCM, data=pcm))

    assert digest.describe() == {
        "kind": "data",
        "media_type": PCM,
        "bytes": len(pcm),
        "sha256": hashlib.sha256(pcm).hexdigest(),
        "seconds": round(len(pcm) / 48000, 3),
    }


def test_a_stream_is_described_as_the_whole_it_adds_up_to() -> None:
    pcm = bytes(range(200)) * 100
    pieces, whole = AudioDigest(), AudioDigest()
    for start in range(0, len(pcm), 4800):
        pieces.add(DataPart(media_type=PCM, data=pcm[start : start + 4800]))
    whole.add(DataPart(media_type=PCM, data=pcm))

    assert pieces.describe() == whole.describe()


def test_a_format_that_states_no_rate_has_no_duration() -> None:
    assert duration("audio/mpeg", 1000) is None
    assert duration(PCM, 48000) == 1.0


def test_the_stored_answer_carries_the_description_in_place_of_the_audio() -> None:
    audio = DataPart(media_type=PCM, data=b"\x01\x02" * 10)
    payload = canonical_to_gemini(
        CanonicalResponse(model="tts", text="", audio=audio, usage=USAGE)
    ).model_dump(exclude_none=True)
    encoded = payload["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]

    stored = strip_attachments(payload)

    assert stored["candidates"][0]["content"]["parts"] == [
        {
            "inlineData": {
                "kind": "data",
                "media_type": PCM,
                "bytes": 20,
                "sha256": hashlib.sha256(audio.data).hexdigest(),
                "seconds": duration(PCM, 20),
            }
        }
    ]
    assert encoded not in json.dumps(stored)
    # The caller's copy is untouched.
    assert payload["candidates"][0]["content"]["parts"][0]["inlineData"]["data"] == encoded


# == the mock ====================================================================================


async def test_the_mock_speaks_one_text_one_way_and_a_longer_text_for_longer() -> None:
    mock = MockProvider("tts")

    short = await mock.generate(_canonical("Hallo."))
    again = await mock.generate(_canonical("Hallo."))
    longer = await mock.generate(_canonical("Guten Morgen, das ist ein Test."))

    assert short.audio is not None and longer.audio is not None
    assert short.audio == again.audio
    assert short.audio.media_type == SPEECH_MEDIA_TYPE
    assert len(longer.audio.data) > len(short.audio.data)
    assert longer.usage.completion_tokens > short.usage.completion_tokens


async def test_the_mock_streams_pieces_that_join_to_its_answer() -> None:
    mock = MockProvider("tts")
    request = _canonical("Guten Morgen, das ist ein Test.")

    whole = await mock.generate(request)
    chunks = [chunk async for chunk in mock.stream_generate(request)]

    joined = b"".join(chunk.audio_delta.data for chunk in chunks if chunk.audio_delta)
    assert whole.audio is not None and joined == whole.audio.data
    assert len([chunk for chunk in chunks if chunk.audio_delta]) > 1
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage == whole.usage


# == through the route ===========================================================================

SPOKEN = {
    "contents": [{"role": "user", "parts": [{"text": "Guten Morgen, das ist ein Test."}]}],
    "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": VOICE},
}


def _app():
    from aira_gateway.app import create_app
    from aira_gateway.config import GatewaySettings

    return create_app(GatewaySettings(auth_required=False, enforce_budgets=False, log_queue_size=0))


async def _declare(app, *capabilities: str, model: str = "mock-1") -> None:
    from aira_gateway.db.models import ModelRead

    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model=model, capabilities=list(capabilities)))
        await session.commit()


async def _rows(app) -> list:
    from aira_gateway.db.models import RequestLog

    async with app.state.db_sessionmaker() as session:
        return list((await session.execute(select(RequestLog))).scalars().all())


def _described(audio: bytes) -> dict:
    return {
        "kind": "data",
        "media_type": SPEECH_MEDIA_TYPE,
        "bytes": len(audio),
        "sha256": hashlib.sha256(audio).hexdigest(),
        "seconds": duration(SPEECH_MEDIA_TYPE, len(audio)),
    }


async def test_a_spoken_answer_reaches_the_caller_and_only_its_description_is_stored() -> None:
    from fastapi.testclient import TestClient

    app = _app()
    with TestClient(app) as client:
        await _declare(app, "speech")
        response = client.post("/v1beta/models/mock-1:generateContent", json=SPOKEN)
        rows = await _rows(app)

    assert response.status_code == 200, response.text
    part = response.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
    audio = base64.b64decode(part["data"])
    assert part["mimeType"] == SPEECH_MEDIA_TYPE and audio
    (row,) = rows
    assert row.response_payload["candidates"][0]["content"]["parts"] == [
        {"inlineData": _described(audio)}
    ]


async def test_a_streamed_spoken_answer_is_described_as_the_whole_it_adds_up_to() -> None:
    from fastapi.testclient import TestClient

    app = _app()
    with TestClient(app) as client:
        await _declare(app, "speech")
        whole = client.post("/v1beta/models/mock-1:generateContent", json=SPOKEN).json()
        streamed = client.post("/v1beta/models/mock-1:streamGenerateContent?alt=sse", json=SPOKEN)
        rows = await _rows(app)

    events = [
        json.loads(line[5:]) for line in streamed.text.splitlines() if line.startswith("data:")
    ]
    pieces = [
        part["inlineData"]["data"]
        for event in events
        for part in event["candidates"][0]["content"]["parts"]
        if "inlineData" in part
    ]
    audio = b"".join(base64.b64decode(piece) for piece in pieces)
    assert len(pieces) > 1, "a stream that arrives whole is not a stream"
    assert audio == base64.b64decode(
        whole["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
    )
    (streamed_row,) = [row for row in rows if row.operation == "streamGenerateContent"]
    assert streamed_row.response_payload["audio"] == _described(audio)


async def test_a_model_that_does_not_speak_is_refused_by_name() -> None:
    from fastapi.testclient import TestClient

    app = _app()
    with TestClient(app) as client:
        await _declare(app, "generate")
        response = client.post("/v1beta/models/mock-1:generateContent", json=SPOKEN)

    assert response.status_code == 400, response.text
    assert "speech" in response.json()["error"]["message"]


async def test_a_text_request_to_a_speech_only_model_says_what_to_send() -> None:
    from fastapi.testclient import TestClient

    app = _app()
    with TestClient(app) as client:
        await _declare(app, "speech")
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json={"contents": [{"role": "user", "parts": [{"text": "Hallo."}]}]},
        )

    assert response.status_code == 400
    assert "responseModalities" in response.json()["error"]["message"]


async def test_a_speech_only_model_is_listed_with_the_verbs_it_answers() -> None:
    from fastapi.testclient import TestClient

    app = _app()
    with TestClient(app) as client:
        await _declare(app, "speech")
        listed = client.get("/v1beta/models/mock-1").json()

    assert "generateContent" in listed["supportedGenerationMethods"]
    assert "embedContent" not in listed["supportedGenerationMethods"]


def test_the_gemini_wire_can_ask_for_speech_and_the_others_cannot() -> None:
    """Declared per adapter (`FRD-624`): Google's wire has the words, Anthropic's API and a chat
    completion do not."""
    from aira_gateway.upstreams.gemini import GeminiUpstream
    from aira_gateway.upstreams.openai.adapter import OpenAIAdapter
    from aira_gateway.upstreams.vertex.adapters import VertexAnthropicAdapter, VertexGeminiAdapter

    assert VertexGeminiAdapter.speaks is True
    assert GeminiUpstream.speaks is True
    assert OpenAIAdapter.speaks is False
    assert VertexAnthropicAdapter.speaks is False
