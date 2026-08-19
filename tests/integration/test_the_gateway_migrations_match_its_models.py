"""The gateway's Alembic migrations describe its SQLAlchemy models, with nothing pending.

Management has had this check since 2026-08-12, when `makemigrations --check` — run for an
unrelated reason — found a pending `AlterField` that had been sitting there since `FRD-308`
shipped. The gateway has thirty-nine Alembic revisions and had no equivalent, so the same defect
had nowhere to be noticed on the plane where it costs more: Django would at least *refuse* an
unmigrated model on `migrate`, while SQLAlchemy declares its tables in Python and simply issues a
query naming a column the database does not have. That surfaces as a `ProgrammingError` on the
request path, in production, on whichever endpoint touches the new column first.

Deliberately in the integration layer and not in the hermetic one. The comparison has to run
against **Postgres**, because that is what the migrations are written for and what a deployment
runs: on SQLite — which `AIRA_TEST_DATABASE` swaps in and which enforces no column lengths — a
comparison reports differences that exist only in the stand-in, and a check that cries wolf is a
check somebody adds a skip to. The rule this repository already paid for: a stand-in more
permissive than the thing it replaces is worse than no stand-in.

What it does *not* do is compare against the live gateway database's current state, which may
legitimately be mid-upgrade. It builds a schema from the migrations in a throwaway database and
compares that with the models.
"""

from __future__ import annotations

import pathlib
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from aira_gateway.config import GatewaySettings
from aira_gateway.db import models as _models  # noqa: F401  (register tables on Base.metadata)
from aira_gateway.db.base import Base

ROOT = pathlib.Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration

#: Indexes `compare_metadata` cannot compare, **by name**, each with what checks it instead.
#:
#: Alembic reflects a descending index column as a `UnaryExpression` and has no way to match that
#: against a declared `postgresql_ops={"created_at": "DESC"}`. It therefore reports every such
#: index as *remove and re-add*, on every run, forever. That is the tool's blind spot rather than
#: a difference, and it was verified by reading both sides: `db/models.py` and `0033` declare the
#: same three columns with the same two DESC ops.
#:
#: **Named, not blanketed.** Exempting the *kind* (`add_index`/`remove_index`) would have hidden
#: the real finding this check made on its first run — `ix_request_logs_use_case_created_at`, an
#: index `0008` created, no model declared, and `0033`'s superseding index left behind on the
#: table that takes a row per request. An exemption is a list, never a `return []` (`ADR-0015`).
#: What the exemption gives up is checked by `test_the_paging_index_really_descends` below.
UNCOMPARABLE_INDEXES = {
    "ix_request_logs_use_case_page": "descending columns reflect as expressions (Alembic)",
}


def _name_of(difference: object) -> str:
    """The index name a `compare_metadata` entry is about, or `""` if it is not about an index."""
    if isinstance(difference, tuple) and len(difference) == 2 and "index" in str(difference[0]):
        return getattr(difference[1], "name", "") or ""
    return ""


def _sync_url(settings: GatewaySettings, database: str) -> str:
    """The migrations run over a synchronous driver; the application uses the async one.

    `render_as_string(hide_password=False)` rather than `str(url)`. SQLAlchemy's `__str__` renders
    the password as `***` — a sensible default that produced *"password authentication failed for
    user aira"* here, which reads as wrong credentials rather than as credentials that were never
    sent. Worth the sentence: the masking is the library being careful, and the failure it causes
    points at the database.
    """
    url = sa.engine.make_url(settings.database_url(use_sqlite=False))
    return url.set(drivername="postgresql+psycopg", database=database).render_as_string(
        hide_password=False
    )


@pytest.fixture
def scratch_database(monkeypatch: pytest.MonkeyPatch) -> str:
    """A throwaway database, created and dropped around the comparison.

    **`AIRA_POSTGRES_DB` is set, not `sqlalchemy.url`.** `migrations/env.py` builds its engine from
    `GatewaySettings()` and never reads the Alembic config's URL — deliberate, and its docstring
    says so — which means `command.upgrade(config, "head")` with a URL set on the config runs
    against *whatever the environment points at*. The first version of this fixture did exactly
    that: the upgrade went to the running stack's own database (a no-op, since it is at head), the
    scratch database stayed empty, and every table in the model was reported as missing. The check
    looked like it had found thirty-nine defects and had found its own wiring.

    That near-miss is the reason for the name: a crashed run leaves an obvious orphan called
    `aira_migration_check_…`, and nothing this test does can touch the database under test.
    """
    settings = GatewaySettings()
    name = f"aira_migration_check_{uuid.uuid4().hex[:8]}"
    admin = sa.create_engine(_sync_url(settings, "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    monkeypatch.setenv("AIRA_POSTGRES_DB", name)
    try:
        yield name
    finally:
        with admin.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_the_models_register_tables_at_all() -> None:
    """A guard on the guard: an empty metadata compares equal to an empty schema, and the check
    below would report perfect agreement about nothing."""
    assert len(Base.metadata.tables) > 5, sorted(Base.metadata.tables)


def test_the_scratch_database_is_the_one_that_gets_migrated(scratch_database: str) -> None:
    """The wiring the first version got wrong, asserted rather than assumed.

    If `env.py` ever stops following `AIRA_POSTGRES_DB`, the check below silently compares the
    models against an empty database and reports every table as pending — a red test for a reason
    that has nothing to do with the schema, which is the kind nobody trusts twice.
    """
    assert GatewaySettings().postgres_db == scratch_database


def test_nothing_is_pending_between_the_migrations_and_the_models(scratch_database: str) -> None:
    url = _sync_url(GatewaySettings(), scratch_database)

    config = Config(str(ROOT / "gateway" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "gateway" / "migrations"))
    command.upgrade(config, "head")

    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            existing = sa.inspect(connection).get_table_names()
            assert len(existing) > 5, (
                f"the migrations produced {existing} in the scratch database — they ran somewhere "
                "else, and every model would be reported as missing"
            )
            context = MigrationContext.configure(connection)
            differences = [
                d
                for d in compare_metadata(context, Base.metadata)
                if _name_of(d) not in UNCOMPARABLE_INDEXES
            ]
    finally:
        engine.dispose()

    assert not differences, (
        "The gateway's models and its migrations disagree — `alembic revision --autogenerate` "
        "would write these:\n  " + "\n  ".join(repr(d) for d in differences) + "\n\n"
        "Unlike Django, SQLAlchemy will not refuse to start over this: it declares the table in "
        "Python and issues a query naming a column the database does not have, which arrives as a "
        "`ProgrammingError` on the request path of whichever endpoint touches it first."
    )


def test_the_paging_index_really_descends(scratch_database: str) -> None:
    """What the exemption above gives up, checked where Alembic cannot look.

    `ix_request_logs_use_case_page` is exempt from the comparison because a descending column
    reflects as an expression, so an exemption without this would let the index quietly lose its
    `DESC` — and the trace view's keyset page (`FRD-502`) would go on working while sorting the
    hottest table in the system on every request. Asked of Postgres's own definition instead.
    """
    url = _sync_url(GatewaySettings(), scratch_database)

    config = Config(str(ROOT / "gateway" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "gateway" / "migrations"))
    command.upgrade(config, "head")

    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            definition = connection.execute(
                sa.text(
                    "select indexdef from pg_indexes "
                    "where indexname = 'ix_request_logs_use_case_page'"
                )
            ).scalar_one()
    finally:
        engine.dispose()

    assert "created_at DESC" in definition, definition
    assert "id DESC" in definition, definition


def test_the_superseded_index_is_gone(scratch_database: str) -> None:
    """`0039` dropped it, and a re-added one would be maintained on every audit row for nothing.

    Kept as its own assertion rather than left to the comparison: the comparison would catch it
    only while the models continue not to declare it, and somebody adding it back *to the models*
    would make both sides agree about an index whose whole story is that it is redundant.
    """
    url = _sync_url(GatewaySettings(), scratch_database)

    config = Config(str(ROOT / "gateway" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "gateway" / "migrations"))
    command.upgrade(config, "head")

    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            names = {
                row[0]
                for row in connection.execute(
                    sa.text("select indexname from pg_indexes where tablename = 'request_logs'")
                )
            }
    finally:
        engine.dispose()

    assert "ix_request_logs_use_case_page" in names, sorted(names)
    assert "ix_request_logs_use_case_created_at" not in names, (
        "the index `0033` superseded is back; `ix_request_logs_use_case_page` covers the same "
        "leading columns and the extra one costs a write on every request"
    )
