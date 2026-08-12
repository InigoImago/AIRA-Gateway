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

import threading
from collections.abc import Iterator

import pytest
import uvicorn
from google import genai
from google.genai import types

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead

MODEL = "mock-1"


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    """A real socket. The SDK builds its own HTTP client, so there is no transport to inject —
    and pretending otherwise would test a stand-in for the thing whose independence is the point.
    """
    app = create_app(
        GatewaySettings(
            auth_required=False,
            require_use_case=False,
            environment="local",
            demo_mode=True,
            test_database=True,
            log_queue_size=0,
        )
    )
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:  # noqa: ASYNC110 — a thread, not a coroutine
        threading.Event().wait(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]

    import anyio

    async def _seed() -> None:
        async with app.state.db_sessionmaker() as session:
            session.add(
                ModelRead(
                    model=MODEL,
                    numeric_id=9001,
                    capabilities=["generate", "embed"],
                    approved=True,
                )
            )
            await session.commit()

    anyio.from_thread.start_blocking_portal  # noqa: B018 — see below
    import asyncio

    asyncio.run(_seed())

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


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


def test_the_sdk_embeds(client: genai.Client) -> None:
    """`:embedContent`, the verb that has now been the odd one out three times."""
    response = client.models.embed_content(model=MODEL, contents="hallo")

    assert response.embeddings
    assert response.embeddings[0].values
