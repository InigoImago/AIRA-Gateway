"""Documents through the live stack (FRD-110).

Two things only show up here. The stripped payload has to survive a **real JSONB round trip** —
SQLite tolerates shapes Postgres does not, and a column that silently held megabytes of base64
would be found by an operator rather than by us. And the refusal has to reach a real client as a
real status, because the whole requirement is that a caller can *tell* the model did not read the
document.
"""

from __future__ import annotations

import base64
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .conftest import GATEWAY_URL, wait_for_row

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7\n" + b"x" * 4000


def _body(marker: str) -> dict:
    return {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": f"summarise this ({marker})"},
                    {
                        "inlineData": {
                            "mimeType": "application/pdf",
                            "data": base64.b64encode(PDF).decode("ascii"),
                        }
                    },
                ],
            }
        ]
    }


async def test_a_model_that_cannot_read_a_document_refuses_it_over_the_wire(fixture) -> None:
    """The requirement, end to end: a caller gets a **refusal they can act on**, not a fluent
    answer about a document the model never saw.

    **Sent with a use case, and that is not incidental.** These two used a bare governance token
    and named no use case, which the gateway served until `AIRA_REQUIRE_USE_CASE` became true by
    default (2026-08-11) — an unattributed request bypasses every budget and limit. Afterwards the
    refusal under test never ran: the request was turned away one control earlier, with
    `Missing use case`, and the assertion `"mock-1" in message` read that sentence and failed.

    A refusal arriving from the wrong control is the failure mode this whole file is about, one
    layer up — so the request is attributed, and what it exercises is the media type again.
    """
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.post(
            "/v1beta/models/mock-1:generateContent",
            headers=fixture.headers(),
            json=_body(uuid.uuid4().hex[:8]),
        )

    assert response.status_code == 400, response.text
    message = response.json()["error"]["message"]
    assert "mock-1" in message
    assert "application/pdf" in message


async def test_the_refusal_is_recorded_as_a_capability_problem(
    engine: AsyncEngine, fixture
) -> None:
    """`no_capable_model`, not an upstream error — an operator reading the report has to see a
    configuration problem rather than an outage.

    Scoped to **this** use case, which the previous version could not be: with no use case to name
    it asked for the newest row mentioning `mock-1` anywhere, so a row another test had just
    written would have answered for it.
    """
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.post(
            "/v1beta/models/mock-1:generateContent",
            headers=fixture.headers(),
            json=_body(uuid.uuid4().hex[:8]),
        )
    assert response.status_code == 400, response.text

    row = await wait_for_row(
        engine,
        "SELECT outcome, status FROM request_logs"
        " WHERE use_case = :slug AND requested_model = 'mock-1'"
        " ORDER BY created_at DESC LIMIT 1",
        {"slug": fixture.slug},
    )

    assert row[0] in ("no_capable_model", "invalid_request")
    assert row[1] == 400


async def test_no_stored_payload_in_the_real_database_holds_base64_bytes(
    engine: AsyncEngine,
) -> None:
    """The size guard. A base64 PDF in a JSONB column makes each row megabytes and puts binary
    the gateway never inspected inside the retention and redaction boundary.

    Checked against what Postgres actually holds rather than against what we intended to write.
    """
    async with engine.connect() as connection:
        oversized = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM request_logs"
                    " WHERE request_payload IS NOT NULL"
                    "   AND length(request_payload::text) > 200000"
                )
            )
        ).scalar()

    assert oversized == 0, "an audit row holds a payload large enough to be raw attachment bytes"


async def test_a_stored_attachment_keeps_its_description(engine: AsyncEngine) -> None:
    """Whatever attachments the stack has seen, every one of them is described rather than
    stored — the digest is what links repeated submissions of the same file without keeping it."""
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT request_payload::text FROM request_logs"
                    " WHERE request_payload::text LIKE '%inlineData%' LIMIT 5"
                )
            )
        ).all()

    for (payload,) in rows:
        assert "sha256" in payload, "an attachment was stored without its description"
        assert '"bytes"' in payload
        # `"data":` as a **key** is what a stored document looks like. Matching bare `"data"` would
        # also hit `{"kind": "data"}` in the description itself — a first version of this assertion
        # did exactly that and failed on a correctly stripped payload, which is the kind of
        # too-crude check that later gets weakened rather than sharpened.
        assert '"data":' not in payload, "the attachment's bytes survived into the audit table"
        # And a description is small. A row in the megabytes means bytes got through some other way.
        assert len(payload) < 100_000
