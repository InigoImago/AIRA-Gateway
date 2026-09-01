"""A span says what its request *was* — shape, schema, batch and surface.

Four Observability sections named these attributes and **not one of them was set**: `FRD-107` §9
(`aira.api.surface`), `FRD-110` §9 (`aira.request.parts`, `aira.request.attachment_bytes`),
`FRD-112` §9 (`aira.response_schema`, `.digest`) and `FRD-113` §9 (the three embedding ones). The
consequence is not a missing figure — it is somebody building a Grafana panel from the document
and getting an empty one, which reads as *nothing happened* rather than as a wrong query.
`LESSONS.md` §7: **a claim no test can reach is a claim that will be wrong.**

Driven through the **HTTP surface**, not through `describe_on_the_span`, and over
`httpx.ASGITransport` rather than `TestClient`: the client has to run the application in the same
task, or the span this test opens is not the span the request sees and every assertion below would
be about a context that never reached the code (`TestClient` runs the app in a portal thread).
That is the same rule this project keeps arriving at — a test that builds the object under test is
a test of the reader.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest_asyncio
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import create_all
from aira_gateway.db.models import ModelRead

MODEL = "mock-1"
NUMERIC_ID = 4711

#: What this model is declared able to do. Everything the cases below ask of it, and nothing more:
#: a capability the catalogue does not name is refused before dispatch (`FRD-114` FR-7), so a
#: thinner declaration would make these tests assert about a refusal instead of about a request.
_DECLARATION = {
    "numeric_id": NUMERIC_ID,
    "capabilities": ["generate", "embed", "attachments", "structured_output"],
    "attachments": {"media_types": {"application/pdf": {"tokens": 2000}}},
    "embedding": {"task_types": ["RETRIEVAL_DOCUMENT"], "dimensions": [256]},
}
GEMINI = f"/v1beta/models/{MODEL}:generateContent"
EMBED = f"/v1beta/models/{MODEL}:embedContent"
KIRA = "/kira/api/external/chat"

_TEXT = {"contents": [{"role": "user", "parts": [{"text": "hallo"}]}]}

#: A real header, because the gateway sniffs the bytes against the declared media type
#: (`attachments.py`) — and an odd length, so the figure on the span is one only this request
#: could produce.
_PDF = b"%PDF-1.7\n" + b"x" * 200
_ATTACHMENT = base64.b64encode(_PDF).decode("ascii")


class _Recorded:
    """A span of our own, and what the request under it put on it.

    The application is not instrumented here — `AIRA_OTEL_ENABLED` is off in this tier — so the
    span a request would normally decorate is the server span the FastAPI instrumentation opens.
    Opening one here is the same context from the code's point of view (`set_span_attributes`
    reads whatever is current) and it keeps the tier hermetic.
    """

    def __init__(self) -> None:
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))

    async def request(self, client: httpx.AsyncClient, path: str, body: dict[str, Any]) -> Any:
        self.exporter.clear()
        tracer = self.provider.get_tracer("test")
        with tracer.start_as_current_span("POST"):
            response = await client.post(path, json=body)
        return response

    @property
    def attributes(self) -> dict[str, Any]:
        spans = self.exporter.get_finished_spans()
        assert spans, "the test's own span was never closed"
        return dict(spans[0].attributes or {})


@pytest_asyncio.fixture
async def served() -> AsyncIterator[tuple[httpx.AsyncClient, _Recorded]]:
    app = create_app(GatewaySettings(auth_required=False, store_payloads=False))
    await create_all(app.state.db_engine)
    async with app.state.db_sessionmaker() as session:
        # The KIRA surface addresses a model by number, so the read-model has to name one — and
        # every capability these cases exercise has to be declared, or they would be asserting
        # about refusals.
        session.add(ModelRead(model=MODEL, **_DECLARATION))
        await session.commit()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, _Recorded()
    await app.state.db_engine.dispose()


# ═══ which surface ═══════════════════════════════════════════════════════════════════════════════


async def test_the_span_names_the_surface_the_request_came_in_on(
    served: tuple[httpx.AsyncClient, _Recorded],
) -> None:
    """`FRD-107` §9. The audit row and the reporting breakdown had it; the span never did, so a
    trace could not tell the two surfaces apart and *"which clients have migrated"* was a question
    for the database only."""
    client, recorded = served
    response = await recorded.request(client, GEMINI, _TEXT)
    assert response.status_code == 200, response.text
    assert recorded.attributes["aira.api.surface"] == "gemini"


async def test_the_compatibility_surface_names_itself(
    served: tuple[httpx.AsyncClient, _Recorded],
) -> None:
    client, recorded = served
    response = await recorded.request(
        client, KIRA, {"request": {"parts": [{"text": "hallo"}]}, "model_id": NUMERIC_ID}
    )
    assert response.status_code == 200, response.text
    assert recorded.attributes["aira.api.surface"] == "kira"


# ═══ what the request carried ════════════════════════════════════════════════════════════════════


async def test_a_text_request_counts_its_parts_and_claims_no_attachment(
    served: tuple[httpx.AsyncClient, _Recorded],
) -> None:
    """`aira.request.attachment_bytes` is **absent**, not zero: a zero on every ordinary chat
    request turns *"which traffic carries documents"* from an existence check into a comparison,
    which is the decision `_tool_attributes` already made for the tool figures."""
    client, recorded = served
    await recorded.request(client, GEMINI, _TEXT)
    attributes = recorded.attributes
    assert attributes["aira.request.parts"] == 1
    assert "aira.request.attachment_bytes" not in attributes


async def test_an_attachment_puts_its_size_on_the_span(
    served: tuple[httpx.AsyncClient, _Recorded],
) -> None:
    """`FRD-110` §9's whole sentence: *enough to see that a slow request was slow because it
    carried 6 MiB, without recording anything about the content.*"""
    client, recorded = served
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": "was steht drin?"},
                    {"inlineData": {"mimeType": "application/pdf", "data": _ATTACHMENT}},
                ],
            }
        ]
    }
    response = await recorded.request(client, GEMINI, body)
    assert response.status_code == 200, response.text
    attributes = recorded.attributes
    assert attributes["aira.request.parts"] == 2
    assert attributes["aira.request.attachment_bytes"] == len(_PDF)


# ═══ structured output ═══════════════════════════════════════════════════════════════════════════


def _with_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "contents": [{"role": "user", "parts": [{"text": "hallo"}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema},
    }


_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}}
_OTHER_SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}}}


async def test_a_structured_request_carries_a_digest_of_its_schema(
    served: tuple[httpx.AsyncClient, _Recorded],
) -> None:
    """`FRD-112` §9: *so "structured requests are slower / more expensive" is answerable without
    storing schemas.*"""
    client, recorded = served
    response = await recorded.request(client, GEMINI, _with_schema(_SCHEMA))
    assert response.status_code == 200, response.text
    attributes = recorded.attributes
    assert attributes["aira.response_schema"] is True
    assert len(str(attributes["aira.response_schema.digest"])) == 16


async def test_an_unstructured_request_says_nothing_about_a_schema(
    served: tuple[httpx.AsyncClient, _Recorded],
) -> None:
    client, recorded = served
    await recorded.request(client, GEMINI, _TEXT)
    assert "aira.response_schema" not in recorded.attributes
    assert "aira.response_schema.digest" not in recorded.attributes


async def test_the_digest_groups_the_same_schema_and_separates_a_different_one(
    served: tuple[httpx.AsyncClient, _Recorded],
) -> None:
    """A digest that were per-request would answer no question at all — the point is that the
    same shape sent a thousand times is one value to group by."""
    client, recorded = served
    await recorded.request(client, GEMINI, _with_schema(_SCHEMA))
    first = recorded.attributes["aira.response_schema.digest"]
    await recorded.request(client, GEMINI, _with_schema(_SCHEMA))
    again = recorded.attributes["aira.response_schema.digest"]
    await recorded.request(client, GEMINI, _with_schema(_OTHER_SCHEMA))
    other = recorded.attributes["aira.response_schema.digest"]

    assert first == again
    assert first != other


# ═══ embeddings ══════════════════════════════════════════════════════════════════════════════════


async def test_an_embedding_says_how_many_texts_it_carried(
    served: tuple[httpx.AsyncClient, _Recorded],
) -> None:
    client, recorded = served
    response = await recorded.request(
        client, EMBED, {"content": {"parts": [{"text": "eine zeile"}]}}
    )
    assert response.status_code == 200, response.text
    attributes = recorded.attributes
    assert attributes["aira.embedding.batch_size"] == 1
    # Absent rather than zero. The figures are what was **resolved** — a model with a declared
    # default would put that on the span, which is what was sent — and this one declares none, so
    # a caller who named nothing produces no attribute rather than an invented `0`.
    assert "aira.embedding.task_type" not in attributes
    assert "aira.embedding.dimensions" not in attributes


async def test_the_embedding_options_the_caller_named_reach_the_span(
    served: tuple[httpx.AsyncClient, _Recorded],
) -> None:
    client, recorded = served
    response = await recorded.request(
        client,
        EMBED,
        {
            "content": {"parts": [{"text": "eine zeile"}]},
            "taskType": "RETRIEVAL_DOCUMENT",
            "outputDimensionality": 256,
        },
    )
    assert response.status_code == 200, response.text
    attributes = recorded.attributes
    assert attributes["aira.embedding.task_type"] == "RETRIEVAL_DOCUMENT"
    assert attributes["aira.embedding.dimensions"] == 256
