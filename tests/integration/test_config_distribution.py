"""Config distribution from Management to the Gateway over Kafka (FRD-204).

The unit suites cover each hop in isolation — the outbox writer, the relay's publish loop, the
consumer's idempotent apply. What only this test can show is that the hops actually connect:
that the compacted topics exist, that the relay reaches the broker, and that the gateway's
consumer turns a published event into a row in its read-model.

That mattered concretely: `make kafka-topics` used to create two of the five topics, and with
`KAFKA_AUTO_CREATE_TOPICS_ENABLE=false` the api-key, pipeline and budget streams failed
silently on a fresh stack (ADR-0007).

Requires `make up`, `make kafka-topics`, and a running `make consume`.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
MANAGEMENT_DB = "postgresql+psycopg://aira:aira-local@localhost:5432/aira_mgmt"


async def _management_engine() -> AsyncEngine:
    from aira_gateway.db.base import build_engine

    return build_engine(MANAGEMENT_DB)


def _run_relay() -> subprocess.CompletedProcess[str]:
    """Publish pending outbox rows. Run out-of-process so Django uses Postgres, not the
    in-memory SQLite the test settings select while pytest is imported."""
    return subprocess.run(
        ["uv", "run", "python", "manage.py", "relay"],
        cwd=REPO_ROOT / "management" / "backend",
        capture_output=True,
        text=True,
        timeout=120,
    )


async def _wait_for(engine: AsyncEngine, query: str, params: dict[str, str], timeout: float = 30.0):
    """Poll the gateway read-model until the consumer has applied the event."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        async with engine.connect() as connection:
            row = (await connection.execute(text(query), params)).first()
        if row is not None:
            return row
        await asyncio.sleep(0.5)
    return None


async def test_a_use_case_published_by_the_relay_reaches_the_gateway(engine: AsyncEngine) -> None:
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    management = await _management_engine()
    try:
        async with management.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO outbox_outboxevent (topic, key, event_type, payload, created_at)"
                    " VALUES ('aira.usecases', :key, 'usecase.upserted', :payload, now())"
                ),
                {
                    "key": slug,
                    "payload": json.dumps(
                        {
                            "slug": slug,
                            "name": "Integration probe",
                            "description": "",
                            "processing_notes": "",
                        }
                    ),
                },
            )

        relay = _run_relay()
        assert relay.returncode == 0, relay.stderr
        assert "published" in relay.stdout

        row = await _wait_for(
            engine, "SELECT name FROM use_cases WHERE slug = :slug", {"slug": slug}
        )
        assert row is not None, "the gateway consumer did not apply the published use case"
        assert row.name == "Integration probe"
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM use_cases WHERE slug = :slug"), {"slug": slug}
            )
        async with management.begin() as connection:
            await connection.execute(
                text("DELETE FROM outbox_outboxevent WHERE key = :key"), {"key": slug}
            )
        await management.dispose()


async def test_an_api_key_event_reaches_the_gateway_and_authenticates(engine: AsyncEngine) -> None:
    """The api-keys topic was one of the three `make kafka-topics` used to miss."""
    import httpx
    from tests.integration.conftest import GATEWAY_URL

    from aira_gateway.auth import keys

    full, prefix, key_hash = keys.generate_api_key()
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    management = await _management_engine()
    try:
        async with management.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO outbox_outboxevent (topic, key, event_type, payload, created_at)"
                    " VALUES ('aira.api-keys', :key, 'api_key.created', :payload, now())"
                ),
                {
                    "key": prefix,
                    "payload": json.dumps(
                        {
                            "prefix": prefix,
                            "key_hash": key_hash,
                            "subject": "integration-distributed",
                            "use_case": slug,
                            "label": "integration",
                            "status": "active",
                        }
                    ),
                },
            )

        assert _run_relay().returncode == 0

        applied = await _wait_for(
            engine, "SELECT subject FROM api_keys WHERE prefix = :prefix", {"prefix": prefix}
        )
        assert applied is not None, "the api-keys topic did not reach the gateway"

        # The distributed key works over real HTTP and is bound to its use case.
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{GATEWAY_URL}/v1beta/models/mock-1:generateContent",
                json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
                headers={"x-goog-api-key": full},
            )
        assert response.status_code == 200

        async with engine.connect() as connection:
            logged = (
                await connection.execute(
                    text(
                        "SELECT use_case FROM request_logs"
                        " WHERE subject = 'integration-distributed'"
                        " ORDER BY created_at DESC LIMIT 1"
                    )
                )
            ).first()
        assert logged is not None and logged.use_case == slug
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM request_logs WHERE subject = 'integration-distributed'")
            )
            await connection.execute(
                text("DELETE FROM api_keys WHERE prefix = :prefix"), {"prefix": prefix}
            )
        async with management.begin() as connection:
            await connection.execute(
                text("DELETE FROM outbox_outboxevent WHERE key = :key"), {"key": prefix}
            )
        await management.dispose()
