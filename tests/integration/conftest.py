"""Shared fixtures for the stack-dependent tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import stack_addresses
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aira_common.apikeys import generate_api_key
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine

# **Never a literal.** These follow the same `AIRA_PUBLISH_…_PORT` variables that publish the
# stack, through `tools/stack_addresses.py` — so a port moved to dodge a collision with another
# system moves here too. They were `http://127.0.0.1:8001`-shaped constants, and this layer then
# failed against a correctly-running stack with "connection refused", which reads as "nothing is
# up" rather than "you are knocking on the wrong door".
GATEWAY_URL = stack_addresses.url("gateway")
MANAGEMENT_URL = stack_addresses.url("management")
MANAGEMENT_DB = "postgresql+psycopg://aira:aira-local@localhost:5432/aira_mgmt"


async def wait_for_row(engine: AsyncEngine, sql: str, params: dict, timeout: float = 8.0):
    """Poll until a query returns a row, or give up and say why.

    The audit write moved **off the hot path** with `FRD-405`: a bounded queue drained by a worker.
    So a response arriving is not the same event as its row existing, and a test that reads
    immediately is testing the drain rate. This repository has been caught by that three times, and
    a fourth was found by running the whole live suite at once — the 174-case edge suite leaves the
    queue busy, and the next file's single read landed before the drain.

    A poll rather than a sleep: a fixed wait is either too short on a loaded machine or wasted on
    an idle one, and the failure message should say "never arrived", not "was not there yet".
    """
    import asyncio

    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        async with engine.connect() as connection:
            row = (await connection.execute(text(sql), params)).first()
        if row is not None:
            return row
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"no row after {timeout}s: {sql} {params}")
        await asyncio.sleep(0.2)


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


KEYCLOAK_URL = stack_addresses.url("keycloak")
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

# A third account, carrying `it-security`. Added when a live round asked both planes who may stop
# traffic and got different answers: `it-steuerung` sees every figure and writes nothing (PRD
# §154), so the suite needed a caller that may actually act in an incident to test either side.
SECURITY_CLIENT_ID = "aira-integration-tests-security"
SECURITY_CLIENT_SECRET = "integration-tests-security-secret"  # noqa: S105

# A fourth account, carrying `global-admin`. Added by `FRD-209`: proving that a **group** grant
# reaches somebody needs two identities — one that may create a use case and grant access, and one
# with *no oversight* that reaches it only through the group. Since `ADR-0017` this account is
# also the only one that may create a use case at all; the creator becomes its administrator
# directly, which is why the *reached-through-a-group* half needs the second account.
ADMIN_CLIENT_ID = "aira-integration-tests-admin"
ADMIN_CLIENT_SECRET = "integration-tests-admin-secret"  # noqa: S105


@pytest.fixture
async def governance_token() -> str:
    """A real token carrying a governance role (ADR-0009)."""
    return await _token(TEST_CLIENT_ID, TEST_CLIENT_SECRET)


@pytest.fixture
async def member_token() -> str:
    """A real token for an ordinary caller — authenticated, and holding **no organisation-wide
    role** (`ADR-0017`). It carried `use-case-admin` until that stopped being a role; what it has
    now is group membership, which is where its access to a use case comes from."""
    return await _token(MEMBER_CLIENT_ID, MEMBER_CLIENT_SECRET)


@pytest.fixture
async def admin_token() -> str:
    """A real token carrying `global-admin` — may create a use case and grant access to it."""
    return await _token(ADMIN_CLIENT_ID, ADMIN_CLIENT_SECRET)


@pytest.fixture
async def security_token() -> str:
    """A real token carrying `it-security` — the role that may stop traffic and author a rule
    that applies everywhere."""
    return await _token(SECURITY_CLIENT_ID, SECURITY_CLIENT_SECRET)


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

    async def rule(self, **spec: object) -> int:
        """Write an anomaly rule straight into the gateway's read-model (`FRD-500`).

        The distribution path — Management, Kafka, the consumer — has its own test above; what is
        under test here is what the *engine* does with a rule, so this puts one where the engine
        reads it.
        """
        rule_id = _read_model_id()
        values: dict[str, object] = {
            "id": rule_id,
            "use_case": self.slug,
            "name": f"rule-{rule_id}",
            "kind": "refusal_rate",
            "window_minutes": 60,
            "threshold": 50,
            "parameter": None,
            "min_sample": 1,
            "action": "alert",
            "target": "subject",
            "action_minutes": None,
            "throttle_rpm": None,
            "enabled": True,
        }
        values.update(spec)
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO anomaly_rules (id, use_case, name, kind, window_minutes,"
                    " threshold, parameter, min_sample, action, target, action_minutes,"
                    " throttle_rpm, enabled) VALUES (:id, :use_case, :name, :kind,"
                    " :window_minutes, :threshold, :parameter, :min_sample, :action, :target,"
                    " :action_minutes, :throttle_rpm, :enabled)"
                ),
                values,
            )
        return rule_id

    async def events(self) -> list[dict[str, object]]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT rule_name, kind, use_case, target, target_value, observed, threshold,"
                    " sample, action_taken, detail FROM anomaly_events"
                    " WHERE use_case = :slug ORDER BY created_at"
                ),
                {"slug": self.slug},
            )
            return [dict(row._mapping) for row in result]

    async def suspensions(self) -> list[dict[str, object]]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT id, use_case, target, target_value, action, throttle_rpm, expires_at,"
                    " author, reason, lifted_at, lifted_by FROM access_suspensions"
                    " WHERE use_case = :slug OR target_value = :slug ORDER BY created_at"
                ),
                {"slug": self.slug},
            )
            return [dict(row._mapping) for row in result]

    async def suspend(self, **spec: object) -> str:
        """Write a suspension directly, for tests about what the *gate* does with one."""
        import uuid as _uuid
        from datetime import UTC, datetime, timedelta

        row_id = str(_uuid.uuid4())
        values: dict[str, object] = {
            "id": row_id,
            "use_case": self.slug,
            "target": "use_case",
            "target_value": self.slug,
            "action": "block",
            "throttle_rpm": None,
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
            "author": "user:integration-probe",
            "reason": "integration test",
            "lifted_at": None,
            "lifted_by": None,
        }
        values.update(spec)
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO access_suspensions (id, use_case, target, target_value, action,"
                    " throttle_rpm, expires_at, author, reason, lifted_at, lifted_by)"
                    " VALUES (:id, :use_case, :target, :target_value, :action, :throttle_rpm,"
                    " :expires_at, :author, :reason, :lifted_at, :lifted_by)"
                ),
                values,
            )
        return row_id

    async def set_store_payloads(self, store: bool) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text("UPDATE use_cases SET store_payloads = :store WHERE slug = :slug"),
                {"store": store, "slug": self.slug},
            )

    async def enable_tools(self) -> None:
        """Turn on tool calling for this use case (`FRD-131` FR-3, default **off**).

        Written into the read-model directly, like the key and the budgets above: what is under
        test here is the gateway's behaviour, and the toggle's distribution path has its own suite.
        A test that *skipped* when tools were off would report green about nothing — the lesson
        `FRD-207` wrote down when a rule-editor test skipped itself whenever no rules existed.
        """
        async with self.engine.begin() as connection:
            await connection.execute(
                text("UPDATE use_cases SET tools_enabled = true WHERE slug = :slug"),
                {"slug": self.slug},
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
            "DELETE FROM anomaly_events WHERE use_case = :slug",
            "DELETE FROM anomaly_rules WHERE use_case = :slug",
            "DELETE FROM access_suspensions WHERE use_case = :slug OR target_value = :slug",
            "DELETE FROM api_keys WHERE use_case = :slug",
            "DELETE FROM use_cases WHERE slug = :slug",
        ):
            await connection.execute(text(statement), {"slug": slug, "like": f"%{slug}%"})


@pytest.fixture
async def governed(engine: AsyncEngine):
    """A use case that may call a real language model **and** a real embedding model.

    Separate from `fixture` above, and the difference is `FRD-308`: a use case starts able to call
    nothing, so a fixture that releases no model can only exercise `mock-1` — the test double,
    which is exempt from the release and approval gates and therefore cannot answer a question
    about either. See `tests/integration/governed.py`.
    """
    from . import governed as _governed

    built = await _governed.build(engine)
    yield built
    await _governed.destroy(built)


@pytest.fixture
async def second_governed(engine: AsyncEngine):
    """A second one, for the cases about one use case not reaching another's allowance."""
    from . import governed as _governed

    built = await _governed.build(engine)
    yield built
    await _governed.destroy(built)
