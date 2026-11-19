"""A database restart costs no configuration event and no audit row.

A restart ends every connection the planes keep in their pools. The gateway's consumer met one
such connection with the next event, took the failure for the event's own, skipped it and
committed its offset: a key revoked just after a restart stayed active, for good. The audit writer
dropped the row that met the same connection. The hermetic suite proves the parts on SQLite — the
pool tests a connection before handing it out, the consumer holds an event through a failure that
passes and decides which failures pass by their type and SQLSTATE, the writer writes once more
under the id it chose. Whether they add up to a revocation that holds and a trail without a gap
is a question only a real restart answers, so this test restarts the stack's database.

Everything else on the stack loses its connections with it — Management, the relay, Keycloak — so
the test waits for all of them to answer again before it returns, whatever it found.
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid

import httpx
import pytest
import stack_addresses
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aira_gateway.auth import keys
from aira_gateway.db.base import build_engine

from . import installation, keycloak
from .conftest import (
    ADMIN_CLIENT_ID,
    ADMIN_CLIENT_SECRET,
    GATEWAY_URL,
    MANAGEMENT_DB,
    MANAGEMENT_URL,
    _token,
)
from .test_config_distribution import _queue_outbox_event, _run_relay, _wait_for

pytestmark = pytest.mark.integration

#: The database server both planes and Keycloak use, as **this** stack names it.
POSTGRES = stack_addresses.container("postgres")
#: How long a published event may take to change what the gateway serves.
APPLIED_WITHIN = 60.0
#: How long the database, Keycloak and both planes may take to answer again after the restart.
RECOVERED_WITHIN = 180.0
#: Keycloak's own pool meets each connection the restart ended once, on the request that draws it,
#: and fails that request; only an unbroken run of answers says none is left for the tests after.
KEYCLOAK_ANSWERS_IN_A_ROW = 10


def _restart(name: str) -> None:
    done = subprocess.run(  # noqa: S603 - fixed argv
        ["docker", "restart", name], capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, f"docker restart {name} failed: {done.stderr.strip()}"


async def _until(check, timeout: float, what: str) -> None:
    """Poll ``check`` until it holds; a failure it raises is the dependency not answering yet."""
    deadline = asyncio.get_running_loop().time() + timeout
    last = ""
    while asyncio.get_running_loop().time() < deadline:
        try:
            if await check():
                return
        # A database, an identity provider or a plane that is still starting fails in many ways,
        # and every one of them means "not yet".
        except Exception as exc:  # noqa: BLE001
            last = repr(exc)
        await asyncio.sleep(1.0)
    raise AssertionError(f"{what} did not answer within {timeout:.0f}s: {last}")


async def _database_answers(engine: AsyncEngine) -> bool:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return True


async def _the_stack_answers() -> bool:
    """Keycloak issues tokens in master and in the realm, Management answers with one, and the
    gateway is ready."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        for _ in range(KEYCLOAK_ANSWERS_IN_A_ROW):
            await keycloak.admin_headers(client)
            token = await _token(ADMIN_CLIENT_ID, ADMIN_CLIENT_SECRET)
        me = await client.get(
            f"{MANAGEMENT_URL}/api/v1/me", headers={"Authorization": f"Bearer {token}"}
        )
        ready = await client.get(f"{GATEWAY_URL}/readyz")
    return me.status_code == 200 and ready.status_code == 200


async def _generate(key: str, slug: str) -> int:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/uc/{slug}/v1beta/models/mock-1:generateContent",
            json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
            headers={"x-goog-api-key": key},
        )
    return response.status_code


async def _recorded(engine: AsyncEngine, subject: str) -> int:
    async with engine.connect() as connection:
        return int(
            (
                await connection.execute(
                    text("SELECT count(*) FROM request_logs WHERE subject = :subject"),
                    {"subject": subject},
                )
            ).scalar_one()
        )


async def test_a_key_revoked_after_a_database_restart_stops_working_and_no_request_is_lost(
    engine: AsyncEngine,
) -> None:
    full, prefix, key_hash = keys.generate_api_key()
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    installation.remember("read_model", slug)
    subject = installation.subject("itest-restart")
    management = build_engine(MANAGEMENT_DB)
    try:
        await _queue_outbox_event(
            management,
            "aira.usecases",
            slug,
            "usecase.upserted",
            {"slug": slug, "name": "Restart probe", "allowed_models": ["mock-1"]},
        )
        await _queue_outbox_event(
            management,
            "aira.api-keys",
            prefix,
            "api_key.created",
            {
                "prefix": prefix,
                "key_hash": key_hash,
                "subject": subject,
                "use_case": slug,
                "label": "integration",
                "status": "active",
            },
        )
        assert _run_relay().returncode == 0
        applied = await _wait_for(
            engine,
            "SELECT 1 FROM api_keys WHERE prefix = :prefix AND is_active",
            {"prefix": prefix},
            timeout=APPLIED_WITHIN,
        )
        assert applied is not None, "the key never reached the gateway"
        assert await _generate(full, slug) == 200

        _restart(POSTGRES)
        await _until(lambda: _database_answers(engine), RECOVERED_WITHIN, "the database")

        # The gateway's pools still hold the connections the restart ended; this request is the
        # first to need one, for its key and for its row.
        assert await _generate(full, slug) == 200

        # And the consumer's: the revocation is the first event it applies after the restart.
        await _queue_outbox_event(
            management, "aira.api-keys", prefix, "api_key.revoked", {"prefix": prefix}
        )
        assert _run_relay().returncode == 0

        async def refused() -> bool:
            return await _generate(full, slug) in (401, 403)

        await _until(refused, APPLIED_WITHIN, "the revocation")

        async def both_recorded() -> bool:
            return await _recorded(engine, subject) >= 2

        await _until(both_recorded, APPLIED_WITHIN, "the request made just after the restart")
    finally:
        await _until(_the_stack_answers, RECOVERED_WITHIN, "the stack")
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM request_logs WHERE subject = :subject"), {"subject": subject}
            )
            await connection.execute(
                text("DELETE FROM api_keys WHERE prefix = :prefix"), {"prefix": prefix}
            )
            await connection.execute(
                text("DELETE FROM use_cases WHERE slug = :slug"), {"slug": slug}
            )
        async with management.begin() as connection:
            await connection.execute(
                text("DELETE FROM outbox_outboxevent WHERE key IN (:key, :slug)"),
                {"key": prefix, "slug": slug},
            )
        await management.dispose()
