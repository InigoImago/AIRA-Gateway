"""Speech output through the gateway, against Vertex in the EU (`FRD-624`, `ADR-0026`).

The speech model is catalogued here as a Vertex model in `europe-west1`, where it was measured to
answer. A stack without Vertex credentials serves nothing under that name, and the cases skip.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .conftest import GATEWAY_URL
from .governed import GEMINI, Governed

pytestmark = pytest.mark.integration

SPEECH_MODEL = "gemini-2.5-flash-tts"
REGION = "europe-west1"
BODY = {
    "contents": [{"role": "user", "parts": [{"text": "Guten Morgen, das ist ein Test."}]}],
    "generationConfig": {
        "responseModalities": ["AUDIO"],
        "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}},
    },
}


@pytest_asyncio.fixture
async def speech_model(engine: AsyncEngine, governed: Governed) -> AsyncIterator[str]:
    """The speech model, catalogued, approved and released to this use case only."""
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM model_catalog WHERE model = :model"), {"model": SPEECH_MODEL}
        )
        await connection.execute(
            text(
                "INSERT INTO model_catalog (model, display_name, provider, publisher, platform,"
                " capabilities, addressing, approved, deprecated, hosting, underlying_model)"
                " VALUES (:model, :model, 'vertex', 'google', '', CAST(:caps AS json),"
                " CAST(:addressing AS json), true, false, '', '')"
            ),
            {
                "model": SPEECH_MODEL,
                "caps": json.dumps(["speech"]),
                "addressing": json.dumps({"regions": [REGION]}),
            },
        )
    await governed.release(SPEECH_MODEL)
    await governed.settle()
    yield SPEECH_MODEL
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM model_catalog WHERE model = :model"), {"model": SPEECH_MODEL}
        )


def _skip_without_vertex(response: httpx.Response) -> None:
    if response.status_code == 404:
        pytest.skip(f"nothing on this stack serves {SPEECH_MODEL}: no Vertex credentials")


async def test_a_spoken_answer_comes_from_the_eu_and_the_trail_keeps_only_its_hash(
    governed: Governed, speech_model: str
) -> None:
    response = await governed.generate(BODY, model=speech_model)
    _skip_without_vertex(response)

    assert response.status_code == 200, response.text[:300]
    part = response.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
    audio = base64.b64decode(part["data"])
    assert part["mimeType"].startswith("audio/L16"), part["mimeType"]
    assert len(audio) > 24_000, "less than half a second of speech"

    row = await governed.last_row()
    stored = row["response_payload"]["candidates"][0]["content"]["parts"][0]["inlineData"]
    assert "data" not in stored
    assert stored["sha256"] == hashlib.sha256(audio).hexdigest()
    assert stored["bytes"] == len(audio)
    assert stored["seconds"] > 0.5
    assert row["region"] == REGION
    assert row["completion_tokens"] > 0, "audio is output, and output is billed"


async def test_a_streamed_spoken_answer_arrives_in_pieces_and_is_hashed_whole(
    governed: Governed, speech_model: str
) -> None:
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=120.0) as client:
        response = await client.post(
            f"{GEMINI}/models/{speech_model}:streamGenerateContent?alt=sse",
            json=BODY,
            headers=governed.headers(),
        )
    _skip_without_vertex(response)

    assert response.status_code == 200, response.text[:300]
    events = [
        json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")
    ]
    pieces = [
        base64.b64decode(part["inlineData"]["data"])
        for event in events
        for part in event["candidates"][0]["content"]["parts"]
        if "inlineData" in part
    ]
    assert len(pieces) > 1, "a stream that arrives whole is not a stream"
    audio = b"".join(pieces)

    row = await governed.last_row()
    assert row["operation"] == "streamGenerateContent"
    assert row["response_payload"]["audio"]["sha256"] == hashlib.sha256(audio).hexdigest()
    assert row["response_payload"]["audio"]["bytes"] == len(audio)
