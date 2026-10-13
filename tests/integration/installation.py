"""What an integration session made on the installation, and the clean-up that removes exactly that.

The suite runs against a stack that is also the demo and, in CI, the browser suite's fixture. A use
case, a catalogue entry, a rule, a role, a Keycloak group or a read-model row it leaves behind
changes what the console lists, what the next suite's premises mean ("this account administers
nothing"), and what every later run measures — so each run makes the installation a little less
like the one the tests describe.

**Only what this run made.** Every creation is written into a register at the moment it is made,
*before* the request, so a creation that succeeds and then fails an assertion is still removed.
Nothing is matched by shape: a person may name a use case `itest-x`, and a sweep by pattern is how
a clean-up takes somebody's work with it (`e2e/created.ts` keeps the same contract).

**Whether the tests passed or not.** The session's finaliser removes everything the register
names. The register is a file, so a run that is killed leaves it behind and the next session adopts
it. Two sessions against one stack at the same time are not supported: each would tidy the other.

**And then it checks.** After removing, it asserts that nothing the register names survives in
either plane, that the installation-wide counts taken when the session started — use cases,
catalogue entries, rules, roles, grants, orphaned audit rows, rows dated in the future, Keycloak
groups and clients — have not grown, and that the shared state the suite changes on purpose (IT
Steuerung's permissions, the `mock-1` catalogue row) is what it was. A creation nobody registered
is a creation this check finds.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import stack_addresses
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

from aira_gateway.scopes import Scope

#: The register. One JSON object per line, `{"kind": …, "name": …}`, appended as things are made.
REGISTER = Path(__file__).resolve().parent / ".artifacts" / "created.jsonl"

GATEWAY_DB = stack_addresses.postgres_url(stack_addresses.setting("AIRA_POSTGRES_DB"))
#: Management's database is named in Compose itself (`AIRA_POSTGRES_DB: aira_mgmt`), not by a
#: variable — the same name here, and the address and the account from the owner like the rest.
MANAGEMENT_DB = stack_addresses.postgres_url("aira_mgmt")
MANAGEMENT_URL = stack_addresses.url("management")

#: What the register can name, and what removing each one means:
#:
#: - ``use_case`` — a use case made **through Management**: retired over the API, its tombstone
#:   purged (`purge_test_use_cases`, demo-guarded), and the gateway's rows for it deleted.
#: - ``read_model`` — a use case written **straight into the gateway's read-model**: its rows there.
#: - ``model`` — a catalogue entry made through Management, deleted over the API.
#: - ``catalog_row`` — a catalogue row written straight into the gateway's read-model.
#: - ``rule`` / ``role`` — an anomaly rule id or a role slug made through Management.
#: - ``subject`` — a caller, credential or target value this session invented: the suspensions,
#:   findings and audit rows that name it.
#: - ``group`` / ``client`` — a Keycloak group id or client uuid.
KINDS = (
    "use_case",
    "read_model",
    "model",
    "catalog_row",
    "rule",
    "role",
    "subject",
    "group",
    "client",
)

#: The suite's own accounts. Their suspensions, findings and audit rows made **during** a session
#: are that session's: nothing else signs in as them.
ACCOUNTS = tuple(
    f"service-account-{client}"
    for client in (
        "aira-integration-tests",
        "aira-integration-tests-member",
        "aira-integration-tests-security",
        "aira-integration-tests-admin",
    )
)

#: How long the purge, the deletions and the rule removals may take to travel to the gateway: the
#: relay publishes every `AIRA_RELAY_INTERVAL` (10 s by default), then the consumer applies.
PROPAGATION_SECONDS = 90.0
#: The audit write is asynchronous (`FRD-405`): a request's row may land after the request's
#: test has finished. The sweep waits until the rows it is about to delete have stopped arriving.
AUDIT_QUIET_SECONDS = 2.0
AUDIT_SETTLE_LIMIT_SECONDS = 45.0

_PURGED = re.compile(r"\[purge\] (\d+) purged, (\d+) already gone, (\d+) still live")


def remember(kind: str, name: object) -> None:
    """Write one creation into the register, before it is attempted."""
    assert kind in KINDS, f"{kind!r} is not something the register knows how to remove"
    REGISTER.parent.mkdir(parents=True, exist_ok=True)
    with REGISTER.open("a", encoding="utf-8") as register:
        register.write(json.dumps({"kind": kind, "name": str(name)}) + "\n")


def remembered() -> dict[str, list[str]]:
    """Everything the register names, by kind, each name once and in the order it was written."""
    found: dict[str, list[str]] = {kind: [] for kind in KINDS}
    if not REGISTER.exists():
        return found
    for line in REGISTER.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        names = found.setdefault(entry["kind"], [])
        if entry["name"] not in names:
            names.append(entry["name"])
    return found


def subject(prefix: str = "itest") -> str:
    """A caller name nobody else uses, registered so whatever names it is removed."""
    name = f"{prefix}-{uuid.uuid4().hex[:8]}"
    remember("subject", name)
    return name


# == the gateway's rows for one use case ===========================================================


def _gateway_statements() -> list[str]:
    """One `DELETE` per gateway table that names a use case — **derived from the models**.

    Derived, not written out: a hand-written list is one table behind the first time somebody adds
    a table with a `use_case` column, and nothing fails when it is. Derived, that table is covered
    without anybody remembering it.
    """
    import aira_gateway.db.models  # noqa: F401 - registers every table on the metadata
    from aira_gateway.db.base import Base

    statements: list[str] = []
    for table in reversed(Base.metadata.sorted_tables):
        columns = {column.name for column in table.columns}
        clauses = []
        if "use_case" in columns:
            clauses.append("use_case = :slug")
            if "target_value" in columns:
                # A suspension or finding aimed at the use case itself names it here.
                clauses.append("target_value = :slug")
        if "use_case_slug" in columns:
            clauses.append("use_case_slug = :slug")
        if table.name == "budget_usage":
            clauses.append("scope_key = :scope OR starts_with(scope_key, :members)")
        if table.name == "use_cases":
            clauses.append("slug = :slug")
        if clauses:
            statements.append(f"DELETE FROM {table.name} WHERE " + " OR ".join(clauses))  # noqa: S608
    return statements


GATEWAY_STATEMENTS = _gateway_statements()


def _slug_parameters(slug: str) -> dict[str, str]:
    return {
        "slug": slug,
        # The keys budget consumption is counted under, asked of the code that writes them.
        "scope": Scope(slug).usage_key,
        "members": Scope(slug, "").usage_key,
    }


async def delete_gateway_rows(engine: AsyncEngine, slug: str) -> None:
    """Every row the gateway holds for ``slug``, in every table that names a use case."""
    async with engine.begin() as connection:
        for statement in GATEWAY_STATEMENTS:
            await connection.execute(text(statement), _slug_parameters(slug))


# == the session ===================================================================================


def _sync_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def _scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar()


def _admin_token() -> str:
    """A Global Administrator's token, fetched when needed — they live five minutes."""
    from .conftest import ADMIN_CLIENT_ID, ADMIN_CLIENT_SECRET

    response = httpx.post(
        f"{stack_addresses.issuer()}/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": ADMIN_CLIENT_ID,
            "client_secret": ADMIN_CLIENT_SECRET,
        },
        timeout=15.0,
    )
    assert response.status_code == 200, f"no admin token for the clean-up: {response.text}"
    return str(response.json()["access_token"])


#: Installation-wide figures that a session must leave no larger than it found them.
_MANAGEMENT_COUNTS = {
    "Management use cases (live and retired)": "SELECT count(*) FROM usecases_usecase",
    "Management catalogue entries": "SELECT count(*) FROM catalog_model",
    "Management anomaly rules": "SELECT count(*) FROM anomalies_anomalyrule",
    "Management roles": "SELECT count(*) FROM roles_storedrole",
    "Management group grants": "SELECT count(*) FROM usecases_usecasegroupgrant",
    "Management memberships": "SELECT count(*) FROM usecases_usecasemembership",
}
_GATEWAY_COUNTS = {
    "gateway use cases": "SELECT count(*) FROM use_cases",
    "gateway API keys": "SELECT count(*) FROM api_keys",
    "gateway catalogue rows": "SELECT count(*) FROM model_catalog",
    "gateway anomaly rules": "SELECT count(*) FROM anomaly_rules",
    "gateway pipelines": "SELECT count(*) FROM pipeline_configs",
    "audit rows naming a use case the gateway does not have": (
        "SELECT count(*) FROM request_logs r WHERE r.use_case IS NOT NULL"
        " AND NOT EXISTS (SELECT 1 FROM use_cases u WHERE u.slug = r.use_case)"
    ),
    "content reads naming a use case the gateway does not have": (
        "SELECT count(*) FROM payload_access p WHERE p.use_case <> ''"
        " AND NOT EXISTS (SELECT 1 FROM use_cases u WHERE u.slug = p.use_case)"
    ),
    # Retention prunes by age and never reaches a row dated in the future, and a newest-first view
    # opens on it for years.
    "audit rows dated more than a day ahead": (
        "SELECT count(*) FROM request_logs WHERE created_at > now() + interval '1 day'"
    ),
}
#: Shared rows the suite changes on purpose and must put back exactly.
_SHARED_STATE = {
    "IT Steuerung's permissions": (
        MANAGEMENT_DB,
        "SELECT permissions::text FROM roles_storedrole WHERE slug = 'it-steuerung'",
    ),
    "the gateway's mock-1 catalogue row": (
        GATEWAY_DB,
        "SELECT row_to_json(m)::text FROM model_catalog m WHERE model = 'mock-1'",
    ),
    "the gateway's installation budgets": (
        GATEWAY_DB,
        "SELECT coalesce(json_agg(b ORDER BY id), '[]')::text FROM budgets b"
        " WHERE scope = 'installation'",
    ),
}


class Session:
    """The register's owner for one pytest session: a baseline taken first, a clean-up last."""

    def __init__(self) -> None:
        self.started = datetime.now(UTC)
        self.gateway = _sync_engine(GATEWAY_DB)
        self.management = _sync_engine(MANAGEMENT_DB)
        self.baseline = self._counts()
        self.shared = self._shared()

    # -- measuring ---------------------------------------------------------------------------

    def _counts(self) -> dict[str, int]:
        counts = {
            name: int(_scalar(self.management, sql)) for name, sql in _MANAGEMENT_COUNTS.items()
        }
        counts |= {name: int(_scalar(self.gateway, sql)) for name, sql in _GATEWAY_COUNTS.items()}
        counts |= _keycloak_counts()
        return counts

    def _shared(self) -> dict[str, str | None]:
        engines = {GATEWAY_DB: self.gateway, MANAGEMENT_DB: self.management}
        return {name: _scalar(engines[url], sql) for name, (url, sql) in _SHARED_STATE.items()}

    # -- removing ----------------------------------------------------------------------------

    def finish(self) -> None:
        """Remove what the register names, then fail on anything left or grown."""
        problems: list[str] = []
        made = remembered()
        steps: list[tuple[str, Callable[[], list[str]]]] = [
            ("retire and purge the use cases", lambda: self._purge(made["use_case"])),
            ("delete the catalogue entries", lambda: self._delete_api("models", made["model"])),
            ("delete the rules", lambda: self._delete_api("anomaly-rules", made["rule"])),
            ("delete the roles", lambda: self._delete_api("roles", made["role"])),
            ("remove the Keycloak groups and clients", lambda: _remove_from_realm(made)),
            ("wait for the gateway to follow", lambda: self._propagated(made)),
            ("delete the gateway's rows", lambda: self._delete_gateway_rows(made)),
        ]
        for name, step in steps:
            try:
                problems += step()
            except Exception as exc:  # noqa: BLE001 - every step runs, and each failure is said
                problems.append(f"{name}: {type(exc).__name__}: {exc}")

        problems += self._survivors(made)
        problems += self._growth()
        problems += self._changed()
        self.gateway.dispose()
        self.management.dispose()

        if not problems:
            REGISTER.unlink(missing_ok=True)
        assert not problems, (
            "the integration session left the installation different from how it found it "
            f"(register kept at {REGISTER} for the next session):\n- " + "\n- ".join(problems)
        )

    def _purge(self, slugs: list[str]) -> list[str]:
        if not slugs:
            return []
        problems: list[str] = []
        headers = {"Authorization": f"Bearer {_admin_token()}"}
        with httpx.Client(base_url=MANAGEMENT_URL, timeout=30.0, headers=headers) as client:
            for slug in slugs:
                # 404 is "already retired, or never made" — a test that failed before creating.
                response = client.delete(f"/api/v1/use-cases/{slug}/")
                if response.status_code not in (204, 404):
                    problems.append(f"retiring {slug}: {response.status_code} {response.text}")
        # The tombstone (`FRD-607`) is removed by the command the browser suite's purge uses, which
        # refuses outside a demo installation and acts only on retired use cases it is named.
        purged = subprocess.run(  # noqa: S603 - fixed argv, names from this session's register
            [
                "docker",
                "exec",
                "-e",
                "AIRA_DEMO_MODE=true",
                stack_addresses.container("management"),
                "python",
                "-m",
                "django",
                "purge_test_use_cases",
                "--settings=aira_management.config.settings",
                *slugs,
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        verdict = _PURGED.search(purged.stdout)
        if purged.returncode != 0 or verdict is None:
            problems.append(f"purging the tombstones failed: {purged.stderr[-500:]}")
        elif int(verdict.group(3)):
            problems.append(f"use cases still live after retiring them: {purged.stdout[-500:]}")
        return problems

    def _delete_api(self, collection: str, names: list[str]) -> list[str]:
        if not names:
            return []
        problems: list[str] = []
        headers = {"Authorization": f"Bearer {_admin_token()}"}
        with httpx.Client(base_url=MANAGEMENT_URL, timeout=30.0, headers=headers) as client:
            for name in names:
                response = client.delete(f"/api/v1/{collection}/{name}/")
                if response.status_code not in (204, 404):
                    problems.append(f"deleting {collection}/{name}: {response.status_code}")
        return problems

    def _propagated(self, made: dict[str, list[str]]) -> list[str]:
        """Wait until the gateway has applied the purges and deletions sent above."""
        checks = [
            ("use_cases", "slug", made["use_case"]),
            ("model_catalog", "model", made["model"]),
            ("anomaly_rules", "CAST(id AS text)", made["rule"]),
        ]
        deadline = time.monotonic() + PROPAGATION_SECONDS
        while True:
            waiting = [
                f"{table} still holds {count} of this session's rows"
                for table, column, names in checks
                if names
                and (
                    count := int(
                        _scalar(
                            self.gateway,
                            f"SELECT count(*) FROM {table} WHERE {column} = ANY(:names)",  # noqa: S608
                            names=names,
                        )
                    )
                )
            ]
            if not waiting or time.monotonic() > deadline:
                return [
                    f"the gateway did not follow within {PROPAGATION_SECONDS}s: {w}"
                    for w in waiting
                ]
            time.sleep(1.0)

    def _delete_gateway_rows(self, made: dict[str, list[str]]) -> list[str]:
        slugs = made["use_case"] + made["read_model"]
        subjects = made["subject"]
        self._settle(slugs, subjects)
        with self.gateway.begin() as connection:
            for slug in slugs:
                for statement in GATEWAY_STATEMENTS:
                    connection.execute(text(statement), _slug_parameters(slug))
            for name in subjects:
                for statement in (
                    "DELETE FROM access_suspensions WHERE target_value = :name",
                    "DELETE FROM anomaly_events WHERE target_value = :name",
                    "DELETE FROM request_logs WHERE subject = :name",
                    "DELETE FROM payload_access WHERE subject = :name",
                ):
                    connection.execute(text(statement), {"name": name})
            for model in made["catalog_row"]:
                connection.execute(text("DELETE FROM model_catalog WHERE model = :m"), {"m": model})
            # What the suite's own accounts did and had done to them while this session ran.
            for statement in _ACCOUNT_ROWS:
                connection.execute(
                    text(statement),
                    {"since": self.started, "accounts": list(ACCOUNTS), "authors": _authors()},
                )
        return []

    def _settle(self, slugs: list[str], subjects: list[str]) -> None:
        """Wait until the audit rows about to be deleted have stopped arriving."""
        sql = (
            "SELECT count(*) FROM request_logs WHERE use_case = ANY(:slugs)"
            " OR subject = ANY(:subjects) OR (created_at >= :since AND username = ANY(:subjects))"
        )
        names = {"slugs": slugs, "subjects": subjects + list(ACCOUNTS), "since": self.started}
        deadline = time.monotonic() + AUDIT_SETTLE_LIMIT_SECONDS
        seen = int(_scalar(self.gateway, sql, **names))
        while time.monotonic() < deadline:
            time.sleep(AUDIT_QUIET_SECONDS)
            now = int(_scalar(self.gateway, sql, **names))
            if now == seen:
                return
            seen = now

    # -- checking ----------------------------------------------------------------------------

    def _survivors(self, made: dict[str, list[str]]) -> list[str]:
        problems: list[str] = []
        slugs = made["use_case"] + made["read_model"]
        checks = [
            (self.management, "usecases_usecase", "slug", made["use_case"]),
            (self.management, "catalog_model", "name", made["model"]),
            (self.management, "anomalies_anomalyrule", "CAST(id AS text)", made["rule"]),
            (self.management, "roles_storedrole", "slug", made["role"]),
            (self.gateway, "model_catalog", "model", made["catalog_row"] + made["model"]),
        ]
        checks += [
            (self.gateway, table, column, slugs) for table, column in _GATEWAY_USE_CASE_COLUMNS
        ]
        checks += [
            (self.gateway, table, column, made["subject"])
            for table, column in (
                ("access_suspensions", "target_value"),
                ("anomaly_events", "target_value"),
                ("request_logs", "subject"),
            )
        ]
        for engine, table, column, names in checks:
            if not names:
                continue
            count = int(
                _scalar(
                    engine,
                    f"SELECT count(*) FROM {table} WHERE {column} = ANY(:names)",  # noqa: S608
                    names=names,
                )
            )
            if count:
                problems.append(f"{table} still holds {count} row(s) this session made")
        for statement in _ACCOUNT_ROWS:
            remaining = statement.replace("DELETE FROM", "SELECT count(*) FROM", 1)
            count = int(
                _scalar(
                    self.gateway,
                    remaining,
                    since=self.started,
                    accounts=list(ACCOUNTS),
                    authors=_authors(),
                )
            )
            if count:
                problems.append(f"{count} row(s) by or about the suite's accounts: {remaining}")
        return problems

    def _growth(self) -> list[str]:
        after = self._counts()
        return [
            f"{name} grew from {before} to {after[name]}"
            for name, before in self.baseline.items()
            if after.get(name, 0) > before
        ]

    def _changed(self) -> list[str]:
        after = self._shared()
        return [
            f"{name} changed: {before} → {after[name]}"
            for name, before in self.shared.items()
            if after[name] != before
        ]


def _authors() -> list[str]:
    return [f"user:{account}" for account in ACCOUNTS]


#: The rows a session's own accounts leave by acting: a suspension one of them set, a finding
#: about one of them, a request one of them made, a stored prompt one of them read — only those
#: made since this session started.
_ACCOUNT_ROWS = (
    "DELETE FROM access_suspensions WHERE created_at >= :since"
    " AND (author = ANY(:authors) OR target_value = ANY(:accounts))",
    "DELETE FROM anomaly_events WHERE created_at >= :since AND target_value = ANY(:accounts)",
    "DELETE FROM request_logs WHERE created_at >= :since"
    " AND (subject = ANY(:accounts) OR username = ANY(:accounts))",
    "DELETE FROM payload_access WHERE created_at >= :since"
    " AND (subject = ANY(:accounts) OR username = ANY(:accounts))",
)


def _use_case_columns() -> list[tuple[str, str]]:
    import aira_gateway.db.models  # noqa: F401
    from aira_gateway.db.base import Base

    pairs: list[tuple[str, str]] = []
    for table in Base.metadata.sorted_tables:
        for column in ("use_case", "use_case_slug"):
            if column in table.columns:
                pairs.append((table.name, column))
        if table.name == "use_cases":
            pairs.append((table.name, "slug"))
    return pairs


_GATEWAY_USE_CASE_COLUMNS = _use_case_columns()


# == the realm =====================================================================================


def _keycloak_counts() -> dict[str, int]:
    from .keycloak import admin_headers_sync, base

    with httpx.Client(timeout=30.0) as client:
        headers = admin_headers_sync(client)
        groups = client.get(f"{base()}/groups/count", headers=headers)
        clients = client.get(f"{base()}/clients", headers=headers)
    groups.raise_for_status()
    clients.raise_for_status()
    return {"Keycloak groups": int(groups.json()["count"]), "Keycloak clients": len(clients.json())}


def _remove_from_realm(made: dict[str, list[str]]) -> list[str]:
    from .keycloak import admin_headers_sync, base

    problems: list[str] = []
    with httpx.Client(timeout=30.0) as client:
        for kind, collection in (("client", "clients"), ("group", "groups")):
            for identifier in made[kind]:
                # A token per call: the master realm's tokens live sixty seconds.
                response = client.delete(
                    f"{base()}/{collection}/{identifier}", headers=admin_headers_sync(client)
                )
                if response.status_code not in (204, 404):
                    problems.append(
                        f"removing Keycloak {kind} {identifier}: {response.status_code}"
                    )
    return problems
