"""What a dispatched request leaves behind, over the real service (FRD-103, FRD-104, FRD-403).

The hermetic suites assert that the recorder is *called* with the right figures. What only a
running gateway shows is that the row actually lands — after the response has gone out, through
the queue, against Postgres — with the attribution and the cost it should have. Between the call
and the row sit an async worker, a second database session and a JSON column whose null semantics
have already caused one defect.

Streaming is here for the same reason and one more: the settlement and the audit write of a
stream happen inside a generator, and a generator's behaviour when the client stops reading is
not something a TestClient reproduces — it buffers the whole body first.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aira_common.apikeys import generate_api_key
from aira_common.money import to_nanos

from .conftest import GATEWAY_URL

pytestmark = pytest.mark.integration

BODY = {"contents": [{"role": "user", "parts": [{"text": "hallo"}]}]}


async def _use_case_with_key(engine: AsyncEngine, slug: str) -> str:
    """A use case and an API key bound to it. Returns the plaintext key."""
    full, prefix, key_hash = generate_api_key()
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO use_cases (slug, name) VALUES (:slug, :slug)"), {"slug": slug}
        )
        await connection.execute(
            text(
                "INSERT INTO api_keys (id, prefix, key_hash, subject, use_case, label, is_active)"
                " VALUES (:id, :prefix, :hash, :subject, :slug, 'itest', true)"
            ),
            {
                "id": f"{prefix}-rp",
                "prefix": prefix,
                "hash": key_hash,
                "subject": f"itest-{slug}",
                "slug": slug,
            },
        )
    return full


async def _price(engine: AsyncEngine, model: str, inp: str, out: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO model_catalog (model, display_name, provider,"
                " input_price_per_million_nanos, output_price_per_million_nanos)"
                " VALUES (:m, :m, 'mock', :i, :o)"
                " ON CONFLICT (model) DO UPDATE SET"
                " input_price_per_million_nanos = :i, output_price_per_million_nanos = :o"
            ),
            {"m": model, "i": to_nanos(inp), "o": to_nanos(out)},
        )


async def _wait_for_log(engine: AsyncEngine, subject: str, timeout: float = 15.0):
    """The audit write is off the request path (FRD-405 §4.4): the row is certain, its timing is
    not, so this waits rather than reading immediately."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT subject, use_case, auth_method, model, status, operation,"
                        " prompt_tokens, completion_tokens, total_tokens, cost_nanos, latency_ms,"
                        " source_ip, (request_payload IS NOT NULL) AS has_payload"
                        " FROM request_logs WHERE subject = :subject"
                        " ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"subject": subject},
                )
            ).first()
        if row is not None:
            return row
        await asyncio.sleep(0.3)
    return None


async def test_a_dispatched_request_is_recorded_with_its_attribution_and_cost(
    engine: AsyncEngine,
) -> None:
    """Everything the spend reporting will read, written by the real service on the real path."""
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    key = await _use_case_with_key(engine, slug)
    await _price(engine, "mock-1", "1.00", "10.00")

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=20.0) as client:
        response = await client.post(
            "/v1beta/models/mock-1:generateContent",
            json=BODY,
            headers={"x-goog-api-key": key},
        )
    assert response.status_code == 200

    row = await _wait_for_log(engine, f"itest-{slug}")
    assert row is not None, "the request never reached request_logs"

    assert row.use_case == slug
    assert row.auth_method == "api_key"
    assert row.model == "mock-1"
    assert row.status == 200
    assert row.operation == "generateContent"
    assert row.total_tokens == row.prompt_tokens + row.completion_tokens
    assert row.latency_ms is not None
    assert row.source_ip  # the audit trail names who called

    # Priced from the prompt/completion split, not from a total at one rate (FRD-403 §1).
    expected = (
        row.prompt_tokens * to_nanos("1.00") // 1_000_000
        + row.completion_tokens * to_nanos("10.00") // 1_000_000
    )
    assert row.cost_nanos == expected, f"expected {expected} nano-units, got {row.cost_nanos}"


async def test_an_unpriced_model_records_no_cost_rather_than_zero(engine: AsyncEngine) -> None:
    """ "Unknown is not zero" all the way to the stored row: a spend figure summed over these must
    be able to tell "free" from "we do not know"."""
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    key = await _use_case_with_key(engine, slug)
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM model_catalog WHERE model = 'mock-1'"))

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=20.0) as client:
        assert (
            await client.post(
                "/v1beta/models/mock-1:generateContent",
                json=BODY,
                headers={"x-goog-api-key": key},
            )
        ).status_code == 200

    row = await _wait_for_log(engine, f"itest-{slug}")
    assert row is not None
    assert row.cost_nanos is None, "an unpriced request must not be recorded as costing nothing"
    assert row.total_tokens  # the usage itself is still known


async def test_a_streamed_request_is_recorded_once_it_finishes(engine: AsyncEngine) -> None:
    """The streaming path settles and writes its audit row inside a generator, after the headers
    have already gone out. Nothing but a real HTTP stream exercises that ordering."""
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    key = await _use_case_with_key(engine, slug)
    await _price(engine, "mock-1", "1.00", "10.00")

    chunks = 0
    async with (
        httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client,
        client.stream(
            "POST",
            "/v1beta/models/mock-1:streamGenerateContent",
            json=BODY,
            headers={"x-goog-api-key": key},
        ) as response,
    ):
        assert response.status_code == 200
        async for _ in response.aiter_bytes():
            chunks += 1

    assert chunks > 0, "the stream produced nothing"
    row = await _wait_for_log(engine, f"itest-{slug}")
    assert row is not None
    assert row.operation == "streamGenerateContent"
    assert row.total_tokens
    assert row.cost_nanos


async def test_sse_is_served_when_the_client_asks_for_it(engine: AsyncEngine) -> None:
    """The google-genai SDK asks for `?alt=sse` and reads `data:` frames; the default form is a
    JSON array. Serving the wrong one to either caller breaks it in a way no status code shows."""
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    key = await _use_case_with_key(engine, slug)

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        sse = await client.post(
            "/v1beta/models/mock-1:streamGenerateContent?alt=sse",
            json=BODY,
            headers={"x-goog-api-key": key},
        )
        plain = await client.post(
            "/v1beta/models/mock-1:streamGenerateContent",
            json=BODY,
            headers={"x-goog-api-key": key},
        )

    assert sse.headers["content-type"].startswith("text/event-stream")
    assert sse.text.startswith("data: ")

    assert plain.headers["content-type"].startswith("application/json")
    assert plain.text.startswith("[") and plain.text.rstrip().endswith("]")


async def test_a_client_that_stops_reading_still_leaves_the_request_accounted_for(
    engine: AsyncEngine,
) -> None:
    """A client that walks away mid-body still leaves an accounted-for request.

    Honest about what this does and does not prove: the mock upstream is fast and the body small,
    so the server may well have finished writing before the read stops — in which case this
    exercises a normal completion and only confirms the row lands. The *deterministic* proof that
    a genuine `GeneratorExit` still settles and logs is the hermetic
    `test_a_client_that_disconnects_mid_stream_does_not_leak_the_reservation`, which drives the
    response iterator and closes it explicitly, and which was verified to fail against the code
    that had the defect.

    It earns its place here anyway: it is the only test that takes a real socket away from a real
    server, and a regression that only appears over HTTP would show up nowhere else.
    """
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    key = await _use_case_with_key(engine, slug)

    async with (
        httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client,
        client.stream(
            "POST",
            "/v1beta/models/mock-1:streamGenerateContent",
            json=BODY,
            headers={"x-goog-api-key": key},
        ) as response,
    ):
        assert response.status_code == 200
        async for _ in response.aiter_bytes():
            break  # read one chunk and walk away

    row = await _wait_for_log(engine, f"itest-{slug}")
    assert row is not None, "a disconnected stream vanished from the audit log"
    assert row.operation == "streamGenerateContent"
