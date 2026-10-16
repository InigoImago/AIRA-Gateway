"""The **real** `google-genai` SDK, pointed at our app, doing what a client does.

Every other test of this surface is written by the same hand that wrote the surface, so it agrees
with it by construction — which is how `:streamGenerateContent` came to be missing every dispatch
condition while its own tests were green, and how the KIRA surface came to assemble an answer and
call it a stream. The SDK is the one participant in this repository that never agreed to anything:
it parses what Google's API returns, and it fails if we return something else.

That makes it the right guard for a specific promise — *the Gemini surface stays usable by a Gemini
client* — while work happens on the compatibility surface next door. Both surfaces share
`api/serving.py` and the application's exception handlers, and on 2026-08-12 both of those were
edited for KIRA's sake: a new `KiraError` handler, a 401 that answers a different code depending on
whether a credential was presented. Each was scoped to KIRA paths by hand. "Scoped by hand" is
exactly the kind of claim that wants a witness rather than a reviewer.

Run in-process against the app (`http_options.base_url` at a live test server), because a test that
needs Google's endpoint is a test that runs nowhere.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from google import genai
from google.genai import types
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalChunk, CanonicalUsage
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError, UpstreamModel

MODEL = "mock-1"


@contextmanager
def _served(app: FastAPI, *models: ModelRead) -> Iterator[str]:
    """``app`` on a real socket, with ``models`` catalogued. The SDK builds its own HTTP client, so
    there is no transport to inject — and pretending otherwise would test a stand-in for the thing
    whose independence is the point."""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:  # noqa: ASYNC110 — a thread, not a coroutine
        threading.Event().wait(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]

    async def _seed() -> None:
        async with app.state.db_sessionmaker() as session:
            session.add_all(models)
            await session.commit()

    asyncio.run(_seed())

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _app() -> FastAPI:
    return create_app(
        GatewaySettings(
            auth_required=False,
            require_use_case=False,
            environment="local",
            demo_mode=True,
            test_database=True,
            log_queue_size=0,
        )
    )


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    catalogued = ModelRead(
        model=MODEL, numeric_id=9001, capabilities=["generate", "embed"], approved=True
    )
    with _served(_app(), catalogued) as url:
        yield url


@pytest.fixture
def client(base_url: str) -> genai.Client:
    return genai.Client(
        api_key="not-checked-here",
        http_options=types.HttpOptions(base_url=base_url, api_version="v1beta"),
    )


def test_the_sdk_generates(client: genai.Client) -> None:
    """The plain call. If the response shape drifts, the SDK raises rather than returning odd
    data — which is the whole reason to let it do the parsing."""
    response = client.models.generate_content(model=MODEL, contents="hallo")

    assert response.text
    assert response.usage_metadata is not None
    assert response.usage_metadata.prompt_token_count


@pytest.mark.filterwarnings("error::UserWarning")
def test_the_sdk_streams(client: genai.Client) -> None:
    """`?alt=sse` and `data:` frames, decoded by the client that decides what those mean.

    Asserted as *more than one chunk*, because a surface that sends the whole answer in one frame
    is still valid SSE and is not streaming — the distinction that went unnoticed on the other
    surface for a week. The timing property has its own file; this one is about the SDK being able
    to read the frames at all.

    **A warning is an assertion here** (`filterwarnings("error")`). We sent `finishReason: ""` on
    every intermediate chunk, and the SDK answered each one with `UserWarning: '' is not a valid
    FinishReason` — a hundred lines of complaint for one ordinary answer, and nothing broken, which
    is why no test noticed: our own clients are dicts, and a dict has no opinion about an enum.
    A client's log is part of what we hand somebody, so the SDK's complaints are failures here.
    """
    chunks = list(client.models.generate_content_stream(model=MODEL, contents="hallo"))

    assert len(chunks) > 1, f"the SDK saw {len(chunks)} chunk(s)"
    assert "".join(chunk.text or "" for chunk in chunks)


def test_the_sdk_reads_the_error_envelope(client: genai.Client) -> None:
    """A refusal must still be Google's shape.

    The application grew a `KiraError` handler and a 401 whose code now depends on whether a
    credential was presented, both scoped to the compatibility surface by a path check. If either
    ever leaked, this surface would answer `{"code": …, "message": …}` where the SDK expects
    `{"error": {…}}` — and a client would report "unknown error" for a refusal that names its own
    cause. The SDK's own exception type is the assertion.
    """
    with pytest.raises(genai.errors.APIError) as raised:
        client.models.generate_content(model="no-such-model", contents="hallo")

    assert raised.value.code == 404
    # Parsed out of Google's envelope by the SDK. A KIRA-shaped body leaves this empty.
    assert "not found" in (raised.value.message or "").lower()


def test_the_sdk_carries_which_control_refused_and_the_request_id(client: genai.Client) -> None:
    """`FRD-628` FR-3, as the SDK hands it to a developer: Google's `ErrorInfo` in `details`, which
    the SDK keeps and prints with the exception — so the reason, and the id that finds the request
    in the console, are in the very line a developer reads. The status and message are unchanged."""
    with pytest.raises(genai.errors.APIError) as raised:
        client.models.generate_content(model="no-such-model", contents="hallo")

    (info,) = raised.value.details["error"]["details"]
    assert info["@type"] == "type.googleapis.com/google.rpc.ErrorInfo"
    assert info["reason"] == "MODEL_NOT_FOUND"
    assert info["domain"] == "aira.gateway"
    request_id = info["metadata"]["request_id"]
    assert request_id in str(raised.value)
    assert raised.value.response.headers["x-aira-request-id"] == request_id
    assert raised.value.response.headers["x-aira-reason"] == "model_not_found"


def test_the_sdk_reads_an_answer_that_says_which_model_answered(base_url: str) -> None:
    """The headers that explain an answer are headers, so the SDK parses the body it always did
    and a client that wants them reads them from the HTTP response (`FRD-628` FR-2)."""
    seen: dict[str, str] = {}

    def keep(response: httpx.Response) -> None:
        seen.update(response.headers)

    sdk = genai.Client(
        api_key="not-checked-here",
        http_options=types.HttpOptions(
            base_url=base_url,
            api_version="v1beta",
            client_args={"event_hooks": {"response": [keep]}},
        ),
    )
    response = sdk.models.generate_content(model=MODEL, contents="hallo")

    assert response.text
    assert seen["x-aira-model"] == MODEL
    assert seen["x-aira-request-id"]


def test_the_sdk_embeds(client: genai.Client) -> None:
    """`:embedContent`, the verb that has now been the odd one out three times."""
    response = client.models.embed_content(model=MODEL, contents="hallo")

    assert response.embeddings
    assert response.embeddings[0].values


@pytest.mark.filterwarnings("error::UserWarning")
def test_the_sdk_can_turn_thinking_off(client: genai.Client) -> None:
    """The configuration a governed gateway sets on nearly every request, from the official client.

    It answered `400 Extra inputs are not permitted` until 2026-08-12. The `google-genai` client
    serialises **every** field in camelCase — `maxOutputTokens`, `topP`, `stopSequences`,
    `responseMimeType`, `systemInstruction` — *except* the two inside `thinkingConfig`, which go
    out as `thinking_budget` and `include_thoughts`. Whether that is a bug in the client does not
    matter: it is what the official client puts on the wire, and nothing on the caller's side can
    change it.

    Written through the SDK rather than as a hand-built body, because a hand-built body is how
    this was missed — every test here sent `thinkingBudget`, which is what we believed the SDK
    sends.
    """
    response = client.models.generate_content(
        model=MODEL,
        contents="hallo",
        config=types.GenerateContentConfig(
            max_output_tokens=24, thinking_config=types.ThinkingConfig(thinking_budget=0)
        ),
    )

    assert response.text


def test_asking_for_the_models_reasoning_is_refused_by_name(client: genai.Client) -> None:
    """The other half, and the one that must **not** become permissive by accident.

    `includeThoughts` asks for the model's reasoning to be returned. Serving that request where the
    use case has not enabled reasoning would answer with no thoughts, a 200, and nothing saying why
    — `FRD-124`'s silent drop, on a field whose entire purpose is to add something to the answer.
    Accepting the snake_case spelling above must not quietly extend to accepting this.

    **The refusal moved but did not soften** (`FRD-135` FR-4). It was absolute and lived in the
    schema; it is now the answer wherever a use case has reasoning off, which is every use case
    until somebody turns it on — including this test's, which configures none. What must not change
    is that the refusal **names the field**: a caller who is told "this use case does not do that"
    without being told *what* has to go looking.
    """
    with pytest.raises(genai.errors.APIError) as raised:
        client.models.generate_content(
            model=MODEL,
            contents="hallo",
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(include_thoughts=True)
            ),
        )

    assert raised.value.code == 400
    assert "includeThoughts" in (raised.value.message or ""), "the refusal must name the field"


def test_include_thoughts_false_is_what_we_already_do(client: genai.Client) -> None:
    """`false` asks for exactly this gateway's behaviour, so refusing it would be refusing
    agreement. Carried, and it means nothing — which is the honest answer rather than a
    convenient one."""
    response = client.models.generate_content(
        model=MODEL,
        contents="hallo",
        config=types.GenerateContentConfig(
            max_output_tokens=24,
            thinking_config=types.ThinkingConfig(include_thoughts=False, thinking_budget=0),
        ),
    )

    assert response.text


# == a stream that breaks off ======================================================================


class _BreaksOff:
    """A model whose connection resets after half an answer."""

    is_test_double = True

    def models(self) -> list[UpstreamModel]:
        return [
            UpstreamModel(
                "breaks-1", "1", ("generateContent", "streamGenerateContent"), provider="test"
            )
        ]

    async def generate(self, request):  # noqa: ANN001, ANN201
        raise NotImplementedError

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        yield CanonicalChunk(
            text_delta="Die erste Hälfte ",
            usage=CanonicalUsage(prompt_tokens=3, completion_tokens=2),
        )
        yield CanonicalChunk(
            text_delta="der Antwort", usage=CanonicalUsage(prompt_tokens=3, completion_tokens=4)
        )
        raise UpstreamError("upstream connection reset", status_code=503)

    async def embed(self, request):  # noqa: ANN001, ANN201
        return [[0.0]]


@pytest.fixture(scope="module")
def breaking_url() -> Iterator[str]:
    app = _app()
    app.state.providers = ProviderRegistry([_BreaksOff()])
    catalogued = ModelRead(model="breaks-1", capabilities=["generate"], approved=True)
    with _served(app, catalogued) as url:
        yield url


def test_a_stream_that_breaks_off_is_an_error_in_the_sdk(breaking_url: str) -> None:
    """The status went out as `200` with the first chunk, so the failure can only travel inside the
    stream — as Google's error element, which the SDK checks every chunk for and raises. A stream
    that merely stopped iterates to its end without complaint, and the application shows half an
    answer as the answer."""
    client = genai.Client(
        api_key="not-checked-here",
        http_options=types.HttpOptions(base_url=breaking_url, api_version="v1beta"),
    )
    received: list[str] = []
    with pytest.raises(genai.errors.APIError) as raised:
        for chunk in client.models.generate_content_stream(model="breaks-1", contents="hallo"):
            received.append(chunk.text or "")

    assert raised.value.code == 503
    assert "".join(received) == "Die erste Hälfte der Antwort"


def test_the_json_array_ends_in_the_error_element(breaking_url: str) -> None:
    """Gemini REST without `alt=sse` streams one JSON array. Closed with `]` after the last chunk,
    it parses as a complete answer; the failure is its last element instead."""
    response = httpx.post(
        f"{breaking_url}/v1beta/models/breaks-1:streamGenerateContent",
        json={"contents": [{"role": "user", "parts": [{"text": "hallo"}]}]},
        timeout=10,
    )

    elements = json.loads(response.text)
    assert [
        element["candidates"][0]["content"]["parts"][0]["text"] for element in elements[:-1]
    ] == [
        "Die erste Hälfte ",
        "der Antwort",
    ]
    error = elements[-1]["error"]
    assert (error["code"], error["message"], error["status"]) == (
        503,
        "upstream connection reset",
        "UNAVAILABLE",
    )
    # Google's own `ErrorInfo`, naming what failed and the request it was (`FRD-628` FR-3).
    (info,) = error["details"]
    assert info["@type"] == "type.googleapis.com/google.rpc.ErrorInfo"
    assert info["reason"] == "UPSTREAM_ERROR"
    assert info["metadata"]["request_id"] == response.headers["x-aira-request-id"]


# == an attachment, as the SDK encodes it ==========================================================

#: A PNG whose bytes fall on the two letters the base64 alphabets disagree about, so the SDK's
#: URL-safe encoding carries `-` and `_`.
PNG = b"\x89PNG\r\n\x1a\n" + b"\xfb\xff\xfe\xff" * 32


@pytest.fixture(scope="module")
def reading() -> Iterator[tuple[FastAPI, str]]:
    app = _app()
    catalogued = ModelRead(
        model=MODEL,
        numeric_id=9001,
        capabilities=["generate", "attachments"],
        attachments={"media_types": {"image/png": {"tokens": 258}}},
        approved=True,
    )
    with _served(app, catalogued) as url:
        yield app, url


def test_the_sdk_attaches_an_image(reading: tuple[FastAPI, str]) -> None:
    """`Part.from_bytes` is how Google's documentation attaches a file, and the SDK serialises it
    in the URL-safe base64 alphabet — which Google's API reads and this surface refused as "not
    valid base64" for any file whose bytes map onto `-` or `_`. The witness file never sent bytes,
    so it never saw it. The stored description has to measure the real file too."""
    app, url = reading
    client = genai.Client(
        api_key="not-checked-here",
        http_options=types.HttpOptions(base_url=url, api_version="v1beta"),
    )

    response = client.models.generate_content(
        model=MODEL,
        contents=[types.Part.from_bytes(data=PNG, mime_type="image/png"), "Was zeigt das Bild?"],
    )

    assert response.text

    async def _stored() -> list[object]:
        async with app.state.db_sessionmaker() as session:
            return [
                row.request_payload for row in (await session.execute(select(RequestLog))).scalars()
            ]

    (stored,) = asyncio.run(_stored())
    described = json.dumps(stored)
    assert f'"bytes": {len(PNG)}' in described
    assert hashlib.sha256(PNG).hexdigest() in described
