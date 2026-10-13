"""Shared fixtures for the stack-dependent tests."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
import seed_local_catalog
import stack_addresses
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aira_common.apikeys import generate_api_key
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine

from . import installation, keycloak

# Both databases, at the published address and as the account Compose creates — re-exported
# for the suites that read Management's.
from .installation import GATEWAY_DB
from .installation import MANAGEMENT_DB as MANAGEMENT_DB

# **Never a literal.** These follow the same `AIRA_PUBLISH_…_PORT` variables that publish the
# stack, through `tools/stack_addresses.py` — so a port moved to dodge a collision with another
# system moves here too. They were `http://127.0.0.1:8001`-shaped constants, and this layer then
# failed against a correctly-running stack with "connection refused", which reads as "nothing is
# up" rather than "you are knocking on the wrong door".
GATEWAY_URL = stack_addresses.url("gateway")
MANAGEMENT_URL = stack_addresses.url("management")

#: The integer a KIRA client sends for the demo's chat model.
#:
#: **Imported from the seed that writes it, not typed here.** Six tests carried `9001` as a
#: literal, and moving the demo onto the predecessor's own id (`1004`, `FRD-107`) would have left
#: every one of them addressing a model that no longer answers — reported as a `404` about a
#: number, which reads as a broken surface rather than as a stale test. `tools/` is already on the
#: path for this layer, and `test_local_model_measurements_agree.py` is what keeps the *other*
#: seed equal to this one.
LOCAL_CHAT_MODEL_ID = seed_local_catalog.CHAT_NUMERIC_ID

#: And the embedding model's, for the same reason and from the same owner.
#:
#: Added 2026-08-26 with the sweep that found twenty-one typed copies of the *chat* id. `9002` had
#: not moved, so every literal of it was still correct — which is the only difference between it
#: and the number beside it, and not a difference anybody would notice on the day it changed.
LOCAL_EMBED_MODEL_ID = seed_local_catalog.EMBED_NUMERIC_ID


def pytest_configure(config: pytest.Config) -> None:
    """Name the one marker this layer adds, so `-m "integration and not model_output"` works."""
    config.addinivalue_line(
        "markers",
        "model_output: reads what a real model answered — kept apart from the gateway's own facts, "
        "because a model's drift fails it without a defect anywhere in this repository",
    )


def pytest_report_header(config: object) -> list[str]:  # noqa: ARG001
    """Say what `/readyz` thinks **before** the first test, not after forty of them.

    `LESSONS.md` §1 records this exact discriminator being found the hard way for the browser
    suite: *"Ask the health endpoint what it thinks before reading a failure as a defect — this
    project built one that distinguishes degraded from down precisely so that question has an
    answer, and it is the cheapest discriminator between 'the code is wrong' and 'the machine is
    missing a part'."* The lesson was written down and the layer that actually calls a model never
    got the check.

    It cost an hour on 2026-08-26. `test_diagnostics.py` stops the Ollama container on purpose and
    starts it again in a `finally`; a run interrupted between the two leaves it down, and every
    later test then fails with `502 Upstream error: ConnectError` — a symptom that points at the
    gateway, the adapter and the code that was just changed, in that order, and at none of them
    correctly. `/readyz` had said `unreachable: local` the whole time.

    **Reported, never a refusal.** A stack brought up without the `demo` profile has no local model
    and is a supported way to run the rest of this layer — the tests that need one skip themselves.
    Failing here would be a check that cries wolf on a supported path, which `LESSONS.md` §3 says
    is a check nobody reads on the day it is right. A header line is enough: it puts the answer
    above the failures instead of leaving it to be inferred from them.
    """
    try:
        with urllib.request.urlopen(f"{GATEWAY_URL}/readyz", timeout=10) as answer:
            body = json.loads(answer.read())
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return [f"gateway /readyz: unreachable ({type(exc).__name__}) — is the stack up?"]

    fallbacks = body.get("fallbacks") or {}
    if not body.get("degraded") and not fallbacks:
        return [f"gateway /readyz: ready, nothing degraded ({body.get('status')})", *_kira_ids()]
    lines = [f"gateway /readyz: DEGRADED — {body.get('status')}"]
    lines += [f"  {name}: {detail}" for name, detail in sorted(fallbacks.items())]
    lines.append(
        "  a failure below may be this rather than the code. "
        f"`docker start {stack_addresses.container('ollama')}` brings the local model back;"
    )
    lines.append(
        "  test_diagnostics.py stops it on purpose and restarts it in a `finally`, which an "
        "interrupted run skips."
    )
    return [*lines, *_kira_ids()]


def _kira_ids() -> list[str]:
    """Which model each id a KIRA client sends reaches in the gateway's catalogue.

    A KIRA request names its model by number, and a number no model holds is answered `422
    MODEL_NOT_FOUND` — the envelope several validation cases also expect. Said before the first
    test, a missing id explains every KIRA failure below at once instead of each looking like a
    broken surface. Reported, like the line above, never a refusal.
    """
    from sqlalchemy import create_engine

    try:
        engine = create_engine(GATEWAY_DB, connect_args={"connect_timeout": 5})
        try:
            with engine.connect() as connection:
                held = {
                    number: connection.execute(
                        text("SELECT model FROM model_catalog WHERE numeric_id = :id"),
                        {"id": number},
                    ).scalar()
                    for number in (LOCAL_CHAT_MODEL_ID, LOCAL_EMBED_MODEL_ID)
                }
        finally:
            engine.dispose()
    except Exception as exc:  # noqa: BLE001 - a header line reports, it never stops the session
        return [f"KIRA ids: could not read the gateway's catalogue ({type(exc).__name__})"]
    return [
        f"KIRA id {number}: {model}"
        if model
        else f"KIRA id {number}: held by no model — every KIRA test addressing it fails with "
        "MODEL_NOT_FOUND (tools/seed_local_catalog.py writes it)"
        for number, model in held.items()
    ]


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
async def engine() -> AsyncIterator[AsyncEngine]:
    """An engine against the real gateway database (never the in-memory SQLite).

    At the address the stack publishes it on, as the account Compose creates — the same owner every
    other address here asks, so a moved port moves this too.
    """
    engine = build_engine(GATEWAY_DB)
    try:
        yield engine
    finally:
        await engine.dispose()


# ---- what the session made, and the clean-up that removes it (`installation.py`) -------------


@pytest.fixture(scope="session")
def installation_session() -> Iterator[installation.Session]:
    """The baseline first, the clean-up and its assertions last — whatever the tests did."""
    session = installation.Session()
    yield session
    session.finish()


@pytest.fixture(autouse=True)
def _tidied(request: pytest.FixtureRequest) -> None:
    """Every live-stack test runs inside the session that tidies after it.

    Asked for by marker rather than for every test in this directory: a hermetic check kept beside
    the live ones must not need a stack to run.
    """
    if request.node.get_closest_marker("integration") is not None:
        request.getfixturevalue("installation_session")


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
        # Requested from the issuer both planes trust, never from the bind address: Keycloak
        # writes `iss` from the host a token was requested through, and the example env file
        # binds to 127.0.0.1, where every token would be refused (`stack_addresses.issuer`).
        response = await client.post(
            f"{stack_addresses.issuer()}/protocol/openid-connect/token",
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


#: The member account's own department: a Keycloak group that holds **that account and nobody
#: else**, made for the session and removed after it.
#:
#: The realm also puts the account in `/abteilungen/kundendienst`, and so it puts `ucuser` — the
#: browser suite's "use-case user". A grant to that group made the demo user a member, or an
#: administrator, of every use case a test granted it; left behind, it made every premise of the
#: form "this account administers nothing" depend on how many runs came before.
MEMBER_ACCOUNT = f"service-account-{MEMBER_CLIENT_ID}"
MEMBER_DEPARTMENT_NAME = f"itest-{uuid.uuid4().hex[:6]}"
MEMBER_DEPARTMENT = f"/abteilungen/{MEMBER_DEPARTMENT_NAME}"


@pytest.fixture(scope="session")
def member_department() -> Iterator[str]:
    """Make :data:`MEMBER_DEPARTMENT` and put the member account in it, before any of its tokens.

    A group claim is written when a token is issued, so every member token is asked for *after*
    this exists — which is why `member_token` depends on it.
    """
    path, group = keycloak.department_for(MEMBER_ACCOUNT, MEMBER_DEPARTMENT_NAME)
    assert path == MEMBER_DEPARTMENT
    yield path
    keycloak.remove_group_sync(group)


@pytest.fixture
async def member_token(member_department: str) -> str:
    """A real token for an ordinary caller — authenticated, and holding **no organisation-wide
    role** (`ADR-0017`). What it has is group membership, which is where its access to a use case
    comes from: :data:`MEMBER_DEPARTMENT`, which nobody else is in."""
    del member_department
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


def read_model_id() -> int:
    """An id for a read-model row.

    These tables never generate their own — every row arrives from Management carrying the one it
    has there, so the sequence is never advanced. One range for every helper, so two of them
    cannot hand out colliding ids.
    """
    return 900_000_000 + int(uuid.uuid4().int % 90_000_000)


async def write_suspension(engine: AsyncEngine, *, use_case: str | None, **spec: object) -> str:
    """Write a suspension straight into the gateway, for tests about what the *gate* does with one.

    One writer for every suite. Six hand-written copies of this `INSERT` each named the columns
    themselves, so the next column added to `access_suspensions` would have broken all six apart.
    """
    row_id = str(uuid.uuid4())
    values: dict[str, object] = {
        "id": row_id,
        "use_case": use_case,
        "target": "use_case",
        "target_value": use_case,
        "action": "block",
        "throttle_rpm": None,
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
        "author": "user:integration-probe",
        "reason": "integration test",
        "lifted_at": None,
        "lifted_by": None,
    }
    values.update(spec)
    async with engine.begin() as connection:
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


async def insert_use_case_and_key(
    engine: AsyncEngine,
    slug: str,
    *,
    subject: str,
    released: list[str] | None = None,
    store_payloads: bool = False,
    retention_days: int | None = None,
    label: str = "itest",
) -> tuple[str, str]:
    """A use case and a key bound to it, written straight into the read-model: ``(key, prefix)``.

    Registered before it is written, so the session removes it whatever the test does next. The key
    is a real one — generated by the shared code, so the format and the hash cannot disagree with
    what the gateway validates.
    """
    installation.remember("read_model", slug)
    full_key, prefix, key_hash = generate_api_key()
    installation.remember("subject", prefix)
    # The retention promise is the column's default unless a test makes one: a NULL there is not
    # "none", it is a row the gateway refuses.
    retention = (", retention_days", ", :retention") if retention_days is not None else ("", "")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                f"INSERT INTO use_cases (slug, name, store_payloads, allowed_models{retention[0]})"
                f" VALUES (:slug, :slug, :store, CAST(:models AS json){retention[1]})"
            ),
            {
                "slug": slug,
                "store": store_payloads,
                "retention": retention_days,
                "models": json.dumps(released) if released is not None else None,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO api_keys (id, prefix, key_hash, subject, use_case, label, is_active)"
                " VALUES (:id, :prefix, :hash, :subject, :slug, :label, true)"
            ),
            {
                "id": str(uuid.uuid4()),
                "prefix": prefix,
                "hash": key_hash,
                "subject": subject,
                "slug": slug,
                "label": label,
            },
        )
    return full_key, prefix


class ReadModel:
    """Use cases a test writes straight into the gateway's read-model, removed when it ends."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.slugs: list[str] = []

    async def use_case(
        self,
        slug: str | None = None,
        *,
        released: list[str] | None = None,
        store_payloads: bool = False,
        key: bool = True,
    ) -> tuple[str, str]:
        """``(slug, key)`` — the key's subject is ``itest-<slug>``; ``key=False`` makes none."""
        slug = slug or f"itest-{uuid.uuid4().hex[:8]}"
        self.slugs.append(slug)
        if not key:
            installation.remember("read_model", slug)
            async with self.engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO use_cases (slug, name, store_payloads, allowed_models)"
                        " VALUES (:slug, :slug, :store, CAST(:models AS json))"
                    ),
                    {
                        "slug": slug,
                        "store": store_payloads,
                        "models": json.dumps(released) if released is not None else None,
                    },
                )
            return slug, ""
        installation.remember("subject", f"itest-{slug}")
        full_key, _ = await insert_use_case_and_key(
            self.engine,
            slug,
            subject=f"itest-{slug}",
            released=released,
            store_payloads=store_payloads,
        )
        return slug, full_key

    async def destroy(self) -> None:
        for slug in self.slugs:
            await installation.delete_gateway_rows(self.engine, slug)


@pytest.fixture
async def read_model(engine: AsyncEngine):
    """Use cases written straight into the gateway's read-model, with keys, removed afterwards."""
    made = ReadModel(engine)
    yield made
    await made.destroy()


class Fixture:
    """A use case, a key bound to it, and whatever limits the test needs.

    The key's owner, :attr:`subject`, is **this fixture's own name**: fixtures sharing one caller
    would add their refusals up into a caller the demo's global rule reports as refused over and
    over — findings about nobody, kept as evidence.
    """

    def __init__(self, engine: AsyncEngine, slug: str, key: str, subject: str) -> None:
        self.engine = engine
        self.slug = slug
        self.key = key
        self.subject = subject
        self._suspensions: list[str] = []

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
                    "id": read_model_id(),
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
        rule_id = read_model_id()
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
                    "SELECT rule_id, rule_name, kind, use_case, target, target_value, observed,"
                    " threshold, sample, action_taken, detail FROM anomaly_events"
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
        """Write a suspension directly, for tests about what the *gate* does with one.

        Removed by its id when the fixture ends, which is what reaches a row aimed at somebody
        else's use case or subject — a teardown by this fixture's slug does not.
        """
        row_id = await write_suspension(self.engine, **{"use_case": self.slug, **spec})
        self._suspensions.append(row_id)
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
                    " request_payload, response_payload, subject, use_case, credential,"
                    " request_bytes, latency_ms"
                    " FROM request_logs WHERE use_case = :slug ORDER BY created_at, id"
                ),
                {"slug": self.slug},
            )
            return [dict(row._mapping) for row in result]

    async def wait_for_rows(self, count: int, *, timeout: float = 25.0) -> list[dict[str, object]]:
        """Wait until ``count`` audit rows exist, and fail if they never do.

        The write is off the request path (`FRD-405`): a response arriving and its row existing are
        two events. Asking for the count the test produced is the only version that cannot lie in
        either direction — fewer returns early, and a timeout says how many arrived.
        """
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            rows = await self.rows()
            if len(rows) >= count:
                return rows
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError(f"expected {count} audit rows, saw {len(rows)}: {rows}")
            await asyncio.sleep(0.25)

    async def destroy(self) -> None:
        async with self.engine.begin() as connection:
            for row_id in self._suspensions:
                await connection.execute(
                    text("DELETE FROM access_suspensions WHERE id = :id"), {"id": row_id}
                )
        await installation.delete_gateway_rows(self.engine, self.slug)


@pytest.fixture
async def fixture(engine: AsyncEngine):
    """A use case with an API key bound to it, cleaned up afterwards.

    The key is written straight into the read-model rather than issued through Management, for the
    same reason the other suites do it: what is under test here is the *gateway's* behaviour, and
    the issuance path has its own suite.
    """
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    subject = installation.subject("itest-probe")
    full_key, _ = await insert_use_case_and_key(
        engine, slug, subject=subject, store_payloads=True, label="governed-path"
    )
    made = Fixture(engine, slug, full_key, subject)
    yield made
    await made.destroy()


#: A model **measured** to emit real tool calls (`FRD-131`). Not the one `make showcase` and CI
#: pull — those take the two smallest models there are, on purpose — so a stack that serves the
#: rest of this layer may well not serve this one. Asked for only through `tool_model`
#: (`test_the_suite_states_its_preconditions.py` holds every file to that).
TOOL_MODEL = "qwen2.5:3b"


@pytest.fixture
async def tool_model(fixture) -> str:
    """Skip unless this stack actually serves :data:`TOOL_MODEL` — asked **before** the request.

    A 404 after it would also mean a model retired, mistyped or never catalogued, and a skip that
    swallowed those would hide the failures worth seeing. Requesting the fixture is visible in a
    signature, which is what the guard can check; an inline skip is not.
    """
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{GATEWAY_URL}/v1beta/models", headers=fixture.headers())
    response.raise_for_status()
    served = {model.get("name", "").removeprefix("models/") for model in response.json()["models"]}
    if TOOL_MODEL not in served:
        pytest.skip(
            f"this stack does not serve {TOOL_MODEL}; it serves {sorted(served)}. "
            f"Pull it with: docker exec {stack_addresses.container('ollama')} ollama pull "
            f"{TOOL_MODEL}"
        )
    return TOOL_MODEL


@pytest.fixture
async def mock_catalogue_row(engine: AsyncEngine):
    """The shared `mock-1` catalogue row, **put back exactly as it was found**.

    Several suites price the deterministic double, take its price away, or declare it a document
    reader — and the row is the installation's, read by every later test and by the browser suite
    that runs next. Left as the last test set it, whether `mock-1` is priced after a run would
    depend on which test ran last.
    """
    from sqlalchemy import delete, select
    from sqlalchemy.dialects.postgresql import insert

    from aira_gateway.db.models import ModelRead

    table = ModelRead.__table__
    async with engine.connect() as connection:
        found = (await connection.execute(select(table).where(table.c.model == "mock-1"))).first()
    snapshot = dict(found._mapping) if found is not None else None
    yield
    async with engine.begin() as connection:
        if snapshot is None:
            await connection.execute(delete(table).where(table.c.model == "mock-1"))
        else:
            await connection.execute(
                insert(table)
                .values(**snapshot)
                .on_conflict_do_update(index_elements=[table.c.model], set_=snapshot)
            )


async def declare_mock(engine: AsyncEngine, **fields: object) -> None:
    """Change the `mock-1` catalogue row — only inside a test that asked for `mock_catalogue_row`,
    which puts it back. Inserted with the double's provider when the stack has no row for it."""
    from sqlalchemy.dialects.postgresql import insert

    from aira_gateway.db.models import ModelRead

    table = ModelRead.__table__
    async with engine.begin() as connection:
        await connection.execute(
            insert(table)
            .values(model="mock-1", display_name="mock-1", provider="mock", **fields)
            .on_conflict_do_update(index_elements=[table.c.model], set_=fields)
        )


#: How a catalogue declares a model that reads PDFs (`FRD-110`) — the double answers by saying it
#: saw the attachment, so a request it serves is one that carried the document all the way.
READS_PDF: dict[str, object] = {
    "capabilities": ["generate", "attachments"],
    "attachments": {"media_types": {"application/pdf": {"tokens": 2000}}},
}


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
