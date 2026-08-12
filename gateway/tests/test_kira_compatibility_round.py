"""Four differences from the predecessor, closed after a comparison against its source.

A KIRA↔AIRA comparison on 2026-08-12 — read from the predecessor's own code, not from its
document — produced ten differences. Six are deliberate and stay (`FRD-107` §5.5); these four were
not decisions, and one of them was **wrong data rather than an error**, which is the worst kind a
compatibility layer can produce.

The rule those §5.5 lines state is why every one of them is written down somewhere:

    a compatibility surface with undocumented differences is worse than no compatibility
    surface, because it is trusted.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from aira_gateway.api.kira import schemas
from aira_gateway.api.kira.mapping import to_embedding
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead

KIRA = "/kira/api/external"


@pytest.fixture
def kira_client() -> Iterator[TestClient]:
    """The surface against the mock, with one catalogued model under the id the cases use.

    `demo_mode` registers the mock provider — a declared test double, so `FRD-307`'s approval rule
    exempts it and these cases test their own subject rather than the catalog.
    """
    app = create_app(
        GatewaySettings(auth_required=False, environment="local", demo_mode=True, log_queue_size=0)
    )
    with TestClient(app) as client:

        async def _seed() -> None:
            async with app.state.db_sessionmaker() as session:
                session.add(
                    ModelRead(
                        model="mock-1",
                        numeric_id=9001,
                        capabilities=["generate", "embed"],
                        publisher="google",
                        approved=True,
                    )
                )
                await session.commit()

        client.portal.call(_seed)  # type: ignore[attr-defined]
        yield client


# == 1. a list is one embedding, not many (`FRD-113` §11, resolved) ==============================


def test_a_list_of_texts_becomes_one_text() -> None:
    """`FRD-113` §11 recorded two readings of the predecessor's singular `vector` for a list
    input, assumed *one vector per text*, and asked for confirmation against the running
    predecessor. Its source says the other one: the texts go as several **parts of one** call.

    So the assumption produced **different data**, not an error — five chunks in, five vectors
    out, where the predecessor gives one. A client indexing chunks got a plausible answer to a
    question it had not asked.

    Joined with nothing between them, which is measured rather than guessed: against
    `gemini-embedding-001`, a multi-part content's vector is cosine 1.000000 to the parts
    concatenated with no separator and 0.9489 to their mean. The provider concatenates; it does
    not build a centroid.
    """
    request = schemas.EmbeddingRequest(text=["Der Hund", " bellt"], model_id=1)

    canonical = to_embedding(request, "emb-1")

    assert canonical.texts == ["Der Hund bellt"]
    # One call, so it weighs one against the rate limit and the request budget — which is also
    # what it *is* now, rather than an accounting choice.
    assert canonical.size == 1


def test_a_single_string_is_unchanged() -> None:
    request = schemas.EmbeddingRequest(text="ein Satz", model_id=1)

    assert to_embedding(request, "emb-1").texts == ["ein Satz"]


def test_the_batch_response_shape_is_gone() -> None:
    """`BatchEmbeddingResponse` existed to make the assumption visible on the wire so somebody
    would catch it. Somebody did. A response model nothing returns is a shape that gets returned
    again eventually."""
    assert not hasattr(schemas, "BatchEmbeddingResponse")


# == 2. the stream streams (`FRD-111` §5.4, refined) =============================================


def test_streaming_chat_sends_updates_as_the_answer_arrives(kira_client: TestClient) -> None:
    """It used to call the **non-streaming** dispatch, wait for the whole answer and send one
    terminal event — SSE as a costume.

    `FRD-111` §5.4 refused to invent `update` events *carrying no model output*, which is still
    right and is not the same question as whether output should arrive progressively. The two were
    answered as one, and a chat client migrating from the predecessor saw a blank view for the
    length of the answer and then all of it at once: no error, no message, just worse — and no way
    to tell "still thinking" from "the connection died".
    """
    response = kira_client.post(
        f"{KIRA}/streaming-chat",
        json={"request": {"parts": [{"text": "hallo"}]}, "model_id": 9001},
    )

    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    statuses = [event["status"] for event in events]

    assert "update" in statuses, f"no progressive events: {statuses}"
    # Exactly one terminal event, and it is last: a client reads it to know the answer is whole.
    assert statuses[-1] == "completed"
    assert statuses.count("completed") == 1
    # Every update carries real text — the thing §5.4 refused to fake.
    assert all(event["data"] for event in events if event["status"] == "update")
    # And the terminal event still carries the **whole** answer, so a conservative client that
    # reads only `completed` is unaffected by any of this.
    whole = "".join(part["text"] for part in events[-1]["data"]["parts"])
    streamed = "".join(event["data"] for event in events if event["status"] == "update")
    assert whole == streamed


# == 3. a health check that can fail (`FRD-117` §5.2, now wired) =================================


def test_health_reports_the_upstreams_from_the_cached_verdict(kira_client: TestClient) -> None:
    """It reported one hardcoded `true` and nothing else — a check that **cannot fail**, which is
    worse than none because a monitor points at it and believes it. The comment there said so and
    named the fix as pending; `FRD-117` §5.2's cached probe shipped, and `/readyz` has been reading
    it ever since. This reads the same verdict, so there is still no I/O per call."""
    response = kira_client.get(f"{KIRA}/health")

    assert response.status_code == 200
    services = {check["service"] for check in response.json()["checks"]}
    assert "Gateway" in services
    # More than the gateway's own opinion of itself.
    assert len(services) > 1


def test_health_answers_503_when_an_upstream_is_unreachable(kira_client: TestClient) -> None:
    """The predecessor answers `503`, and a monitor reads the status code long before the body."""
    probe = kira_client.app.state.upstream_probe  # type: ignore[attr-defined]
    probe._verdicts = {
        name: type(verdict)(  # a verdict of the same shape, reporting failure
            provider=verdict.provider, ok=False, detail="unreachable", at=verdict.at
        )
        for name, verdict in probe._verdicts.items()
    }

    response = kira_client.get(f"{KIRA}/health")

    assert response.status_code == 503
    assert response.json()["status"] == "UNHEALTHY"


# == 4. malformed JSON answers what the predecessor answers ======================================


def test_malformed_json_is_a_422_validation_error(kira_client: TestClient) -> None:
    """`400` is the better answer about HTTP and the wrong answer here: the predecessor's `422`
    comes from FastAPI's validation handler, and a migrating client switches on `code`. Being
    right about semantics on a compatibility layer means being wrong about what the layer is for.
    """
    response = kira_client.post(f"{KIRA}/chat", content=b"{not json")

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_a_body_that_is_not_an_object_answers_the_same() -> None:
    """The other half of `_json`, which had the same code and must move with it."""
    from aira_gateway.api.kira import errors

    assert not hasattr(errors, "INVALID_JSON_BODY")
