"""Four defects an edge-case sweep against a running gateway found (2026-08-06).

Each reached a deployed system and none was visible to a suite that only sends requests it already
believes in. They live here, hermetically, so CI catches their return in thirty seconds rather than
in the next live sweep — a defect found once at the outer layer belongs in the innermost one that
can hold it.

The theme they share is the sweep's own rule: **a caller's mistake must not become our error**. Two
of them produced a 500 for a malformed body, one silently changed an answer, and one billed for a
request that asked nothing.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead

GENERATE = "/v1beta/models/mock-1:generateContent"
KIRA = "/kira/api/external"


def _app(**settings: Any):  # noqa: ANN201
    return create_app(GatewaySettings(auth_required=False, log_queue_size=0, **settings))


async def _catalogue(app, **fields: Any) -> None:  # noqa: ANN001
    defaults = {"numeric_id": 9001, "capabilities": ["generate", "embed"]}
    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model="mock-1", **{**defaults, **fields}))
        await session.commit()


# == 1. a request that asks nothing ==============================================================


@pytest.mark.parametrize(
    "contents",
    [
        [{"role": "user", "parts": []}],
        [{"role": "user", "parts": [{"text": ""}]}],
        [{"role": "user", "parts": [{"text": "   "}]}],
        [{"role": "user", "parts": [{"text": "\n\t "}]}],
        [{"role": "user", "parts": [{"text": ""}]}, {"role": "model", "parts": [{"text": ""}]}],
    ],
)
async def test_a_request_with_no_content_is_refused_rather_than_billed(
    contents: list[dict],
) -> None:
    """`FRD-113` FR-7 already refuses an empty *embedding* input and names the reason — it
    prevents a class of accidental no-op billing. The same argument was never applied to
    generation, so `parts: []` was served, charged, and answered with whatever a model says to
    nothing. Found by sending exactly that."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(GENERATE, json={"contents": contents})

    assert response.status_code == 400, response.text
    assert "no text" in response.json()["error"]["message"]


async def test_an_attachment_alone_is_not_an_empty_request() -> None:
    """The check asks whether the request *carries* anything, not whether it carries prose. A
    caller sending a document and no question is asking something."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(
            app,
            capabilities=["generate", "attachments"],
            attachments={"media_types": {"application/pdf": {"tokens": 10}}},
        )
        response = client.post(
            GENERATE,
            json={
                "contents": [
                    {
                        "parts": [
                            {"inlineData": {"mimeType": "application/pdf", "data": "JVBERi0xLjcK"}}
                        ]
                    }
                ]
            },
        )

    assert response.status_code != 400 or "no text" not in response.text


# == 2. a non-positive output cap ================================================================


@pytest.mark.parametrize("cap", [0, -1, -999_999])
async def test_a_non_positive_output_cap_is_refused_not_applied_as_a_slice(cap: int) -> None:
    """It was accepted, and `words[:limit]` with a negative limit does not mean "no limit" — it
    drops the end of the answer. The caller sees a truncated response, a 200, and no explanation.
    A real vendor would reject it with a message about a field they cannot map to their request."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(
            GENERATE,
            json={
                "contents": [{"parts": [{"text": "hi"}]}],
                "generationConfig": {"maxOutputTokens": cap},
            },
        )

    assert response.status_code == 400, response.text
    assert "positive" in response.json()["error"]["message"]
    assert str(cap) in response.json()["error"]["message"], "the message does not echo the value"


async def test_a_positive_cap_still_works() -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(
            GENERATE,
            json={
                "contents": [{"parts": [{"text": "hi"}]}],
                "generationConfig": {"maxOutputTokens": 8},
            },
        )
    assert response.status_code == 200, response.text


# == 3. a validation detail that could not be serialised =========================================


async def test_a_custom_validator_failure_is_a_422_and_not_a_500() -> None:
    """The KIRA surface returns pydantic's ``errors()`` as the predecessor's ``details``. Whenever
    a **custom** validator raised — and ours does, for "a part carries either text or data" — that
    list carried the original `ValueError` object in ``ctx``, which is not JSON serialisable. The
    response render then raised, and the framework turned a malformed body into a **500**: our
    error, for their mistake, on the one surface whose contract is its error shape."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(f"{KIRA}/chat", json={"request": {"parts": [{}]}, "model_id": 9001})

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"], "the details array is what a client reads to find the field"
    assert "loc" in body["details"][0]


async def test_the_details_do_not_echo_the_callers_input_back() -> None:
    """A habit worth not having: reflecting the offending value eventually reflects a prompt, or a
    credential somebody put in the wrong field, into a response and from there into logs."""
    app = _app()
    secret = "sk-do-not-echo-me"
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(
            f"{KIRA}/chat",
            json={"request": {"parts": [{"text": secret, "mime_type": "x"}]}, "model_id": 9001},
        )

    assert response.status_code == 422
    assert secret not in response.text


# == 4. a shared control's refusal on the compatibility surface ==================================


async def test_a_shared_refusal_is_rendered_in_the_predecessors_vocabulary() -> None:
    """`api/serving` is surface-agnostic by design and raises its own error type. The KIRA
    surface's renderer had no branch for it, so **every** shared control's refusal fell through to
    the catch-all and became a 500 — a control that works but cannot be reported on one of the
    surfaces it protects."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(f"{KIRA}/chat", json={"request": {"parts": []}, "model_id": 9001})

    assert response.status_code == 400, response.text
    body = response.json()
    # The predecessor's envelope, not Google's — that is the whole point of the surface.
    assert "code" in body and "error" not in body
    assert body["code"] == "VALIDATION_ERROR"


async def test_a_model_without_the_capability_is_refused_in_kira_words_too() -> None:
    """A second shared refusal, through the same path, so the branch is not pinned by one case."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["embed"])
        response = client.post(
            f"{KIRA}/chat", json={"request": {"parts": [{"text": "hi"}]}, "model_id": 9001}
        )

    assert response.status_code in (400, 422), response.text
    assert "code" in response.json()


# == 5. a wrong URL gets the envelope of the surface it was aimed at ==============================


@pytest.mark.parametrize(
    ("path", "key"),
    [
        ("/v1beta/models/", "error"),
        ("/v1beta/nonsense", "error"),
        (f"{KIRA}/nonsense", "code"),
        ("/nonsense", "error"),
    ],
)
async def test_an_unroutable_path_answers_in_this_apis_shape(path: str, key: str) -> None:
    """Found by asking what a percent-encoded path traversal returns: the status was right and the
    body was the framework's `{"detail": "Not Found"}` — a different shape from every other error
    the same API produces, handed to the caller least equipped to deal with one, since by
    definition they have not found the right route yet."""
    app = _app()
    with TestClient(app) as client:
        response = client.post(path, json={})

    assert response.status_code in (404, 405), response.text
    assert key in response.json(), f"{path} answered in somebody else's envelope: {response.text}"
