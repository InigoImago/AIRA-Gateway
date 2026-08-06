"""Shared fixtures for the stack-dependent tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine

GATEWAY_URL = "http://127.0.0.1:8001"
MANAGEMENT_URL = "http://127.0.0.1:8002"
MANAGEMENT_DB = "postgresql+psycopg://aira:aira-local@localhost:5432/aira_mgmt"


@pytest.fixture
def settings() -> GatewaySettings:
    return GatewaySettings()


@pytest.fixture
async def engine(settings: GatewaySettings) -> AsyncIterator[AsyncEngine]:
    """An engine against the real gateway database (never the in-memory SQLite)."""
    engine = build_engine(settings.database_url(use_sqlite=False))
    try:
        yield engine
    finally:
        await engine.dispose()


KEYCLOAK_URL = "http://localhost:8080"
REALM = "aira"
# A confidential client with a service account, present only in the dev realm. It exists so the
# integration layer can obtain a **real, realm-signed** token carrying real roles — the thing no
# hermetic test can produce and no e2e test can hand to a non-browser caller.
#
# Deliberately not the password grant: ADR-0007 disabled that, and re-enabling it for tests would
# weaken the realm for a convenience a machine-to-machine grant already provides.
TEST_CLIENT_ID = "aira-integration-tests"
TEST_CLIENT_SECRET = "integration-tests-dev-secret"


async def _token(client_id: str, secret: str) -> str:
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Requested through *localhost* on purpose: Keycloak derives the `iss` claim from the
        # request host, and the gateway compares it against AIRA_OIDC_ISSUER. Asking via
        # 127.0.0.1 yields a token the gateway rejects for a reason that looks nothing like the
        # cause.
        response = await client.post(
            f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": secret,
            },
        )
    if response.status_code != 200:
        raise AssertionError(
            f"No token from the dev realm ({response.status_code}). The realm is imported only "
            f"when it does not already exist, so a stack that predates the '{client_id}' client "
            "still has the old realm — recreate it (see deploy/compose/README.md)."
        )
    return str(response.json()["access_token"])


# A second service account, deliberately *without* an oversight role. An RBAC test with only one
# caller proves nothing about who is excluded, which is the half that matters.
MEMBER_CLIENT_ID = "aira-integration-tests-member"
MEMBER_CLIENT_SECRET = "integration-tests-member-secret"


@pytest.fixture
async def governance_token() -> str:
    """A real token carrying a governance role (ADR-0009)."""
    return await _token(TEST_CLIENT_ID, TEST_CLIENT_SECRET)


@pytest.fixture
async def member_token() -> str:
    """A real token for a use-case admin — authenticated, but with no oversight."""
    return await _token(MEMBER_CLIENT_ID, MEMBER_CLIENT_SECRET)
