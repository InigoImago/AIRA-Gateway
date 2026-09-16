"""A change to what is stored is a change to the privacy notice (`FRD-625` FR-8).

The notice is written from `apps/privacy/activities.py`. This holds that register to the two
schemas it describes — Management's Django models and the gateway's tables — **in both directions**:

1. every column either plane stores is named by an activity or declared not personal. A new
   `user_agent` on `request_logs` fails here until an activity names it, and naming it changes the
   register, which changes the notice's digest, which requires a new edition — so everybody is
   asked to read the notice again;
2. every column the register names exists. A register describing a column that was dropped is a
   notice telling people about data nobody holds, which is wrong in the other direction.

A table declared not personal is not a blind spot either: a column in it shaped like a person
(`PERSON_SHAPED`) fails as well, because a `created_by` added to the model catalogue is exactly the
change nobody thinks of as "processing personal data".

And the permissions: every installation-wide permission is either one the notice lists per role or
declared to reach nobody's data, so a new permission is decided rather than silently omitted from
"who has access".
"""

from __future__ import annotations

from collections import defaultdict

import pytest
from aira_management.apps.privacy.activities import (
    ACTIVITIES,
    NOT_PERSONAL_COLUMNS,
    NOT_PERSONAL_PERMISSIONS,
    NOT_PERSONAL_TABLES,
    PERSON_SHAPED,
    PERSONAL_DATA_PERMISSIONS,
    Plane,
)
from django.apps import apps

from aira_common.permissions import Permission
from aira_gateway.db.models import Base

REGISTER = "management/backend/src/aira_management/apps/privacy/activities.py"

Table = tuple[Plane, str]


def _schema() -> dict[Table, set[str]]:
    """Every table and its columns, as the two planes define them — not as the register says."""
    tables: dict[Table, set[str]] = {}
    for model in apps.get_models(include_auto_created=True):
        tables[(Plane.MANAGEMENT, model._meta.db_table)] = {
            field.column for field in model._meta.concrete_fields
        }
    for table in Base.metadata.sorted_tables:
        tables[(Plane.GATEWAY, table.name)] = {column.name for column in table.columns}
    return tables


def _claimed() -> dict[Table, set[str]]:
    claimed: dict[Table, set[str]] = defaultdict(set)
    for activity in ACTIVITIES:
        for stored in activity.stores:
            claimed[(stored.plane, stored.table)].update(stored.columns)
    for table, columns in NOT_PERSONAL_COLUMNS.items():
        claimed[table].update(columns)
    return claimed


def _name(table: Table) -> str:
    return f"{table[0]}:{table[1]}"


def test_both_schemas_are_read() -> None:
    """A check over an empty schema passes whatever the register says."""
    schema = _schema()
    assert (Plane.GATEWAY, "request_logs") in schema
    assert (Plane.MANAGEMENT, "auth_user") in schema
    assert "source_ip" in schema[(Plane.GATEWAY, "request_logs")]


def test_every_table_is_in_the_register_or_declared_not_personal() -> None:
    claimed = _claimed()
    unaccounted = sorted(
        _name(table)
        for table in _schema()
        if table not in claimed and table not in NOT_PERSONAL_TABLES
    )
    assert not unaccounted, (
        f"{unaccounted} store data the privacy notice does not describe. Name the columns under an "
        f"activity in {REGISTER} and describe them in every texts/*.toml — or, if the table holds "
        "nobody's data, add it to NOT_PERSONAL_TABLES with the reason."
    )


def test_every_column_of_a_personal_table_is_named() -> None:
    schema, claimed = _schema(), _claimed()
    missing = sorted(
        f"{_name(table)}.{column}"
        for table, columns in claimed.items()
        if table in schema
        for column in schema[table] - columns
    )
    assert not missing, (
        f"{missing} are stored and not named in the privacy notice's register. Add each to the "
        f"activity that writes it in {REGISTER} (or to NOT_PERSONAL_COLUMNS if it is about no "
        "person), describe it in every texts/*.toml, and move the notice's edition."
    )


def test_a_person_shaped_column_in_a_table_declared_not_personal_is_not_waved_through() -> None:
    schema = _schema()
    shaped = sorted(
        f"{_name(table)}.{column}"
        for table in NOT_PERSONAL_TABLES
        for column in schema.get(table, set())
        if PERSON_SHAPED.search(column)
    )
    assert not shaped, (
        f"{shaped} look like data about a person, in a table the register declares holds none. "
        f"Move the table into an activity in {REGISTER}."
    )


def test_everything_the_register_names_exists() -> None:
    schema, claimed = _schema(), _claimed()
    ghosts = sorted(
        f"{_name(table)}.{column}"
        for table, columns in claimed.items()
        for column in columns - schema.get(table, set())
    )
    stale = sorted(_name(table) for table in NOT_PERSONAL_TABLES if table not in schema)
    assert not ghosts, f"The privacy notice's register names columns nothing stores: {ghosts}"
    assert not stale, f"NOT_PERSONAL_TABLES names tables that no longer exist: {stale}"


def test_a_table_is_personal_or_not_never_both() -> None:
    both = sorted(_name(table) for table in _claimed() if table in NOT_PERSONAL_TABLES)
    assert not both


@pytest.mark.parametrize("permission", list(Permission))
def test_every_permission_is_decided(permission: Permission) -> None:
    listed = permission in PERSONAL_DATA_PERMISSIONS
    waived = permission in NOT_PERSONAL_PERMISSIONS
    assert listed != waived, (
        f"{permission} is {'both' if listed else 'neither'} listed in the notice's access section "
        f"and declared to reach nobody's data ({REGISTER})."
    )


def test_the_shape_recognises_the_columns_it_exists_for() -> None:
    """A pattern that matched nothing would make the previous check pass for ever."""
    for column in (
        "created_by",
        "requested_by_id",
        "subject",
        "source_ip",
        "user_agent",
        "owner_id",
    ):
        assert PERSON_SHAPED.search(column), column
    for column in ("display_name", "addressing", "window_minutes", "provider", "slug"):
        assert not PERSON_SHAPED.search(column), column
