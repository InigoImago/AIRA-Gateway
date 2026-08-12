"""Three shapes the compatibility surface got wrong, each found by comparing against the
predecessor's source rather than by any test here.

That is the common thread and the reason this file exists. Every other test of this surface was
written by whoever wrote the surface, from the same idea of what the predecessor does — so a shape
somebody *invented* passes its own tests forever. The three below were invented, plausible, and
wrong:

- `/health` answered `{"status": "HEALTHY", "checks": [{service, healthy: bool, tags}]}` where the
  predecessor answers `{"status": "Healthy", "total_time_taken", "entities": [{service, status:
  str, time_taken, tags}]}`. Different key, different field names, different type, different
  casing — a typed client cannot deserialise it at all, on the endpoint a monitoring system reads
  to decide whether to page somebody.
- `INVALID_TOKEN` was declared in the error vocabulary and raised by nothing, so a rejected
  credential and an absent one both answered `NOT_AUTHENTICATED`. Those are a security signal and
  a deployment slip, and one bucket for both is a bucket nobody can act on.
- two text parts of one message were passed through separately where the predecessor joins them
  with a newline. Same request, different prompt, no error anywhere — the expensive kind.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from aira_gateway.api.kira import schemas
from aira_gateway.api.kira.mapping import TEXT_PART_SEPARATOR, to_canonical
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings

KIRA = "/kira/api/external"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(GatewaySettings(auth_required=False, environment="local", log_queue_size=0))
    with TestClient(app) as running:
        yield running


# == /health, in the predecessor's shape =========================================================


def test_health_answers_the_predecessors_shape(client: TestClient) -> None:
    """Field for field, because every one of them differed.

    Asserted on the **body** rather than through our own response model: a test that builds
    `HealthResponse` and compares it against what the route returns is a test of one object
    against itself, which is how the invented shape survived.
    """
    response = client.get(f"{KIRA}/health")

    assert response.status_code == 200
    body = response.json()

    assert body["status"] in ("Healthy", "Unhealthy"), "the predecessor's status is title-cased"
    assert isinstance(body["total_time_taken"], int | float)
    assert "checks" not in body, "the invented key"
    assert body["entities"], "the predecessor calls the list 'entities'"

    entity = body["entities"][0]
    assert set(entity) == {"service", "status", "time_taken", "tags"}
    assert entity["status"] in ("Healthy", "Unhealthy")
    assert isinstance(entity["status"], str), "a status, never a boolean"
    assert isinstance(entity["time_taken"], int | float)


def test_an_unprobed_upstream_says_so_in_its_tags(client: TestClient) -> None:
    """`time_taken` cannot carry "we did not look" and a tag can (`FRD-117`).

    The number for an unprobed adapter is 0.0 because the shape has nothing else, so without the
    tag it would be indistinguishable from an upstream that answered instantly — the exact
    conflation `FRD-117` was written to prevent, arriving through the compatibility surface.
    """
    body = client.get(f"{KIRA}/health").json()
    unprobed = [e for e in body["entities"] if "not-probed" in e["tags"]]

    for entity in unprobed:
        assert entity["time_taken"] == 0.0
    # And the gateway's own check is never "not probed": it performs no I/O and knows it.
    gateway = next(e for e in body["entities"] if e["service"] == "Gateway")
    assert gateway["tags"] == ["aira"]


# == a credential that was rejected is not a credential that was absent ==========================


def _authenticating() -> TestClient:
    return TestClient(
        create_app(GatewaySettings(auth_required=True, environment="local", log_queue_size=0)),
        raise_server_exceptions=False,
    )


BODY = {"request": {"parts": [{"text": "hallo"}]}, "model_id": 9001}


def test_no_credential_answers_not_authenticated() -> None:
    with _authenticating() as client:
        response = client.post(f"{KIRA}/chat", json=BODY)

    assert response.status_code == 401
    assert response.json()["code"] == "NOT_AUTHENTICATED"


def test_a_rejected_credential_answers_invalid_token() -> None:
    """The half that was missing. `INVALID_TOKEN` was in the vocabulary and nothing raised it —
    a declared code emitted by nobody, which this file's neighbour removed `INVALID_JSON_BODY`
    for. Here the answer was to raise it rather than to delete it, because the predecessor draws
    the line and a client's error handling switches on the string."""
    with _authenticating() as client:
        response = client.post(
            f"{KIRA}/chat", json=BODY, headers={"x-goog-api-key": "aira_deadbeef_nope"}
        )

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_TOKEN"


def test_the_gemini_surface_is_unchanged_by_that_distinction() -> None:
    """Google's envelope has no field for either code, and inventing one would make that surface
    non-Gemini. Asserted alongside for the reason this repository asserts both every time: a fix
    that satisfied one surface by changing the other would pass every test about the surface it
    was aimed at."""
    with _authenticating() as client:
        absent = client.post("/v1beta/models/mock-1:generateContent", json={"contents": []})
        rejected = client.post(
            "/v1beta/models/mock-1:generateContent",
            json={"contents": []},
            headers={"x-goog-api-key": "aira_deadbeef_nope"},
        )

    for response in (absent, rejected):
        assert response.status_code == 401
        assert response.json()["error"]["status"] == "UNAUTHENTICATED"


# == several text parts are one prompt, joined the way the predecessor joins them ================


def _request(*texts: str) -> schemas.ChatRequest:
    return schemas.ChatRequest.model_validate(
        {"request": {"parts": [{"text": text} for text in texts]}, "model_id": 1}
    )


def test_two_text_parts_become_one_separated_by_a_newline() -> None:
    canonical = to_canonical(_request("Hallo", "Welt"), "mock-1")

    assert canonical.messages[-1].text == f"Hallo{TEXT_PART_SEPARATOR}Welt"
    assert TEXT_PART_SEPARATOR == "\n"


def test_they_arrive_as_one_part_not_several() -> None:
    """The joining has to happen *here*, not at the adapter.

    Left as separate canonical parts, each dialect renders them its own way — Gemini as several
    parts, the OpenAI dialect concatenated with nothing between — so the same request produced
    `HalloWelt` on one provider and a two-part message on another, and the predecessor's own
    answer (`Hallo\\nWelt`) on neither. A rule that is a property of the *predecessor* belongs on
    the surface that speaks for it.
    """
    canonical = to_canonical(_request("eins", "zwei", "drei"), "mock-1")

    assert len(canonical.messages[-1].parts) == 1


def test_an_attachment_keeps_the_prose_in_its_place() -> None:
    """Only *runs* of text are merged. Joining every text part regardless would pull the prose
    after a document in front of it, which changes what the model is being asked about — a
    reordering is a quieter version of the very defect this fix is for."""
    request = schemas.ChatRequest.model_validate(
        {
            "request": {
                "parts": [
                    {"text": "davor"},
                    {"mime_type": "application/pdf", "data": "JVBERi0xLjcK"},
                    {"text": "danach"},
                ]
            },
            "model_id": 1,
        }
    )

    parts = to_canonical(request, "mock-1").messages[-1].parts

    assert [type(part).__name__ for part in parts] == ["TextPart", "DataPart", "TextPart"]


# == a text part carries text, and nothing is converted on the way through =======================


@pytest.mark.parametrize(
    ("value", "would_have_become"),
    [
        pytest.param(None, "None", id="null"),
        pytest.param(123, "123", id="number"),
        pytest.param(True, "True", id="boolean"),
        pytest.param({"a": 1}, "{'a': 1}", id="object"),
        pytest.param(["a"], "['a']", id="array"),
    ],
)
def test_a_non_string_text_is_refused_rather_than_converted(
    value: object, would_have_become: str
) -> None:
    """The measurement that started this: `str(...)` in the mapper accepted anything.

    `{"text": null}` asked the model about the word **"None"** and answered 200; an object became
    a Python repr on the wire. No error at any point, which is what makes it expensive — a caller
    reads a fluent answer to a question nobody asked and blames the model.

    The refusal names the field and its type, because "validation failed" would send somebody
    looking at the wrong part of a request that looks perfectly reasonable.
    """
    with pytest.raises(ValidationError) as raised:
        schemas.RequestContent.model_validate({"parts": [{"text": value}]})

    message = str(raised.value)
    assert "must be a string" in message
    assert type(value).__name__ in message, "the refusal must name what was sent"
    # And the conversion is never offered as though it were the value: naming `"None"` as what we
    # made of a null would read as a suggestion rather than as a refusal.
    assert f"'{would_have_become}'" not in message.replace("input_value", "")


def test_a_string_still_passes_untouched() -> None:
    """The paired case. A check that refuses everything is not a stricter check, and an assertion
    about a refusal is only defended by one that shows the accepted case still works."""
    content = schemas.RequestContent.model_validate({"parts": [{"text": "123"}]})

    assert content.parts[0]["text"] == "123"


def test_the_refusal_reaches_the_caller_as_this_surfaces_envelope(client: TestClient) -> None:
    """Asserted at the route as well as at the model (`FRD-124`'s lesson, twice over now): the
    rule can be perfectly right in the schema and reach the caller as something else entirely."""
    response = client.post(
        f"{KIRA}/chat", json={"request": {"parts": [{"text": None}]}, "model_id": 9001}
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert "error" not in body
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"], "the predecessor's clients read `details` to find the field"
    # `loc` names the field pydantic was validating; the part index rides in the message, which
    # is how the neighbouring "either text or mime_type" rule already reports and what a
    # migrating client's error display already handles.
    assert body["details"][0]["loc"] == ["request"]
    assert "parts[0]" in body["details"][0]["msg"], "the refusal must point at the part"
    # The caller's own value never comes back out. It is their content, this body goes into logs
    # and screens, and echoing it is how a refusal becomes a second copy of the thing refused.
    assert "input" not in str(body["details"])
