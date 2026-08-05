"""The gateway's HTTP contract against a *running* gateway (ADR-0007).

The unit suite exercises the same rules through an in-process ASGI app. These run over real
HTTP, so they also cover what only exists at that layer: the ASGI middleware chain, the
uvicorn/Starlette request handling, and the actual status codes a client sees.

Requires `make up` plus a gateway on :8001 (see `tests/integration/README.md`).
"""

from __future__ import annotations

import httpx
import pytest
from tests.integration.conftest import GATEWAY_URL

pytestmark = pytest.mark.integration

GENERATE = f"{GATEWAY_URL}/v1beta/models/mock-1:generateContent"
BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


@pytest.fixture
async def client() -> httpx.AsyncClient:
    async with httpx.AsyncClient(timeout=20.0) as client:
        yield client


async def test_healthz_is_open(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{GATEWAY_URL}/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readyz_reports_dependencies_without_naming_hosts(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{GATEWAY_URL}/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    # The probe is unauthenticated: it must not map the internal network.
    assert "postgres" in body["checks"]
    assert ":5432" not in response.text


async def test_generate_requires_a_credential(client: httpx.AsyncClient) -> None:
    response = await client.post(GENERATE, json=BODY)
    assert response.status_code == 401
    assert response.json()["error"]["status"] == "UNAUTHENTICATED"


async def test_dry_run_requires_a_credential(client: httpx.AsyncClient) -> None:
    """It reaches the configured providers, so it must never be open (ADR-0007)."""
    response = await client.post(
        f"{GATEWAY_URL}/v1beta/pipeline:dryRun", json={"user": "hi", "pipeline": {}}
    )
    assert response.status_code == 401


async def test_usage_requires_a_credential(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{GATEWAY_URL}/v1beta/usage/demo-uc")
    assert response.status_code == 401


async def test_invalid_use_case_selector_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{GATEWAY_URL}/v1beta/usage/Not%20A%20Slug")
    # Rejected on shape before authentication even matters.
    assert response.status_code in (400, 401)


async def test_oversized_body_is_rejected_before_buffering(client: httpx.AsyncClient) -> None:
    settings_limit = 8 * 1024 * 1024
    payload = {"contents": [{"parts": [{"text": "x" * (settings_limit + 1024)}]}]}
    response = await client.post(GENERATE, json=payload)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == 413


async def test_an_api_key_authenticates_and_is_attributed(
    client: httpx.AsyncClient, engine: object
) -> None:
    """A key minted straight into the gateway database authenticates over real HTTP."""
    from sqlalchemy import text

    from aira_gateway.auth import keys

    full, prefix, key_hash = keys.generate_api_key()
    async with engine.begin() as connection:  # type: ignore[attr-defined]
        await connection.execute(
            text(
                "INSERT INTO api_keys (id, prefix, key_hash, subject, label, is_active)"
                " VALUES (:id, :prefix, :hash, :subject, :label, true)"
            ),
            {
                "id": prefix + "-integration",
                "prefix": prefix,
                "hash": key_hash,
                "subject": "integration-test",
                "label": "integration",
            },
        )
    try:
        response = await client.post(GENERATE, json=BODY, headers={"x-goog-api-key": full})
        assert response.status_code == 200
        assert response.json()["candidates"][0]["content"]["parts"][0]["text"]

        # The dispatched request is recorded with its attribution (FRD-103).
        async with engine.connect() as connection:  # type: ignore[attr-defined]
            recorded = await connection.execute(
                text(
                    "SELECT subject, auth_method, status FROM request_logs"
                    " WHERE subject = :subject ORDER BY created_at DESC LIMIT 1"
                ),
                {"subject": "integration-test"},
            )
            row = recorded.first()
        assert row is not None
        assert row.auth_method == "api_key"
        assert row.status == 200
    finally:
        async with engine.begin() as connection:  # type: ignore[attr-defined]
            await connection.execute(
                text("DELETE FROM request_logs WHERE subject = 'integration-test'")
            )
            await connection.execute(
                text("DELETE FROM api_keys WHERE prefix = :prefix"), {"prefix": prefix}
            )


async def test_a_revoked_key_stops_working(client: httpx.AsyncClient, engine: object) -> None:
    from sqlalchemy import text

    from aira_gateway.auth import keys

    full, prefix, key_hash = keys.generate_api_key()
    async with engine.begin() as connection:  # type: ignore[attr-defined]
        await connection.execute(
            text(
                "INSERT INTO api_keys (id, prefix, key_hash, subject, label, is_active)"
                " VALUES (:id, :prefix, :hash, 'integration-revoked', 'integration', false)"
            ),
            {"id": prefix + "-revoked", "prefix": prefix, "hash": key_hash},
        )
    try:
        response = await client.post(GENERATE, json=BODY, headers={"x-goog-api-key": full})
        assert response.status_code == 401
    finally:
        async with engine.begin() as connection:  # type: ignore[attr-defined]
            await connection.execute(
                text("DELETE FROM api_keys WHERE prefix = :prefix"), {"prefix": prefix}
            )
