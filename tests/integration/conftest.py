"""Shared fixtures for the stack-dependent tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aira_common.apikeys import generate_api_key
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


# ---- a caller bound to a use case (FRD-205) ------------------------------------------------
#
# Shared by every suite that needs an *attributed* request. Without a use case there is nothing to
# budget, limit, price or report against, so a suite that sends no credential tests only the 401.


def _read_model_id() -> int:
    """These read-model tables never generate their own ids — every row arrives from Management
    carrying the one it has there, so the sequence is never advanced."""
    return 900_000_000 + int(uuid.uuid4().int % 90_000_000)


class Fixture:
    """A use case, a key bound to it, and whatever limits the test needs."""

    def __init__(self, engine: AsyncEngine, slug: str, key: str) -> None:
        self.engine = engine
        self.slug = slug
        self.key = key

    def headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.key, "content-type": "application/json"}

    async def budget(self, **limits: int | None) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO budgets (id, use_case, scope, subject, period, limit_tokens,"
                    " limit_requests, limit_cost_nanos, enabled)"
                    " VALUES (:id, :slug, 'use_case', '', 'month', :tokens, :requests, :cost, true)"
                ),
                {
                    "id": _read_model_id(),
                    "slug": self.slug,
                    "tokens": limits.get("limit_tokens"),
                    "requests": limits.get("limit_requests"),
                    "cost": limits.get("limit_cost_nanos"),
                },
            )

    async def set_store_payloads(self, store: bool) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text("UPDATE use_cases SET store_payloads = :store WHERE slug = :slug"),
                {"store": store, "slug": self.slug},
            )

    async def rows(self) -> list[dict[str, object]]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT operation, status, outcome, model, prompt_tokens, completion_tokens,"
                    " request_payload, response_payload, subject, use_case, credential"
                    " FROM request_logs WHERE use_case = :slug ORDER BY created_at"
                ),
                {"slug": self.slug},
            )
            return [dict(row._mapping) for row in result]


@pytest.fixture
async def fixture(engine: AsyncEngine):
    """A use case with an API key bound to it, cleaned up afterwards.

    The key is written straight into the read-model rather than issued through Management, for the
    same reason the other suites do it: what is under test here is the *gateway's* behaviour, and
    the issuance path has its own suite. The key is a real one — generated by the shared code, so
    the format and the hash cannot disagree with what the gateway validates.
    """
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    full_key, prefix, key_hash = generate_api_key()

    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO use_cases (slug, name, store_payloads) VALUES (:slug, :slug, true)"),
            {"slug": slug},
        )
        await connection.execute(
            text(
                "INSERT INTO api_keys (id, prefix, key_hash, subject, use_case, label, is_active)"
                " VALUES (:id, :prefix, :hash, 'integration-probe', :slug, 'governed-path', true)"
            ),
            {"id": str(uuid.uuid4()), "prefix": prefix, "hash": key_hash, "slug": slug},
        )

    yield Fixture(engine, slug, full_key)

    async with engine.begin() as connection:
        for statement in (
            "DELETE FROM request_logs WHERE use_case = :slug",
            "DELETE FROM budgets WHERE use_case = :slug",
            "DELETE FROM budget_usage WHERE scope_key LIKE :like",
            "DELETE FROM rate_limits WHERE use_case = :slug",
            "DELETE FROM api_keys WHERE use_case = :slug",
            "DELETE FROM use_cases WHERE slug = :slug",
        ):
            await connection.execute(text(statement), {"slug": slug, "like": f"%{slug}%"})
