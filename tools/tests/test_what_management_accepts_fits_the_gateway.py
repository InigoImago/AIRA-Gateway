"""Every text Management accepts fits the gateway column it is applied to.

Management validates a use case, a budget or a model, saves it and publishes it; the gateway's
consumer writes it into a read-model whose columns were declared separately, in the other plane's
code. Where a column is narrower than the field Management accepts, PostgreSQL refuses the **whole**
event: the consumer logs it and moves on, the console shows the configuration saved, and the plane
that enforces it never receives it. SQLite enforces no length, so no hermetic test of either plane
can see the difference — this compares the two declarations instead.

It was not hypothetical. `description` and `processing_notes` took 20 000 characters in Management
and 2000 in the gateway, so a use case with a long description never arrived — its release, its
storage switch and its retirement with it.

## What this reads

The gateway's columns from its SQLAlchemy metadata. Management's bounds from its model source with
`ast`, as `test_a_model_event_is_applied_whole.py` does and for its reason: Django needs a
configured settings module, and this test would then be one such variable away from not running at
all.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from sqlalchemy import String

from aira_gateway.db.models import ApiKey, read_models

ROOT = Path(__file__).resolve().parents[2]
APPS = ROOT / "management/backend/src/aira_management/apps"

#: Every gateway table Management's events write, with the Management model it mirrors.
MIRRORS: dict[str, tuple[str, str]] = {
    "use_cases": ("usecases/models.py", "UseCase"),
    "use_case_members": ("usecases/models.py", "UseCaseMembership"),
    "use_case_groups": ("usecases/models.py", "UseCaseGroupGrant"),
    "pipeline_configs": ("pipelines/models.py", "PipelineConfig"),
    "budgets": ("budgets/models.py", "Budget"),
    "rate_limits": ("ratelimits/models.py", "RateLimit"),
    "anomaly_rules": ("anomalies/models.py", "AnomalyRule"),
    "model_catalog": ("catalog/models.py", "Model"),
    "roles": ("roles/models.py", "StoredRole"),
    "api_keys": ("apikeys/models.py", "ApiKey"),
}

#: `UseCase.slug`, which every column naming a use case carries.
SLUG = ("usecases/models.py", "UseCase", "slug")
#: Django's own `AbstractUser.username`, which this repository does not replace (no
#: `AUTH_USER_MODEL`): the events name a member and a key's owner by it.
USERNAME = ("django", "AbstractUser", "username")
USERNAME_LENGTH = 150

#: A column fed by a field of another name or another model, as `(module, class, field)`.
SOURCES: dict[tuple[str, str], tuple[str, str, str]] = {
    ("use_case_members", "use_case_slug"): SLUG,
    ("use_case_members", "subject"): USERNAME,
    ("use_case_groups", "use_case_slug"): SLUG,
    ("pipeline_configs", "use_case"): SLUG,
    ("budgets", "use_case"): SLUG,
    ("rate_limits", "use_case"): SLUG,
    ("anomaly_rules", "use_case"): SLUG,
    ("model_catalog", "model"): ("catalog/models.py", "Model", "name"),
    ("api_keys", "use_case"): SLUG,
    ("api_keys", "subject"): USERNAME,
}

#: Bounded columns no Management field writes, each with the reason. **A decision, not a waiver**.
NOT_FROM_MANAGEMENT: dict[tuple[str, str], str] = {
    ("use_case_members", "id"): "the row's own key, generated here",
    ("use_case_groups", "id"): "the row's own key, generated here",
    ("api_keys", "id"): "the row's own key, generated here",
}

#: The Django fields that hold text.
TEXT_FIELDS = frozenset({"CharField", "TextField", "SlugField", "EmailField", "URLField"})


def _number(node: ast.expr, constants: dict[str, int], where: str) -> int:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.Name) and node.id in constants:
        return constants[node.id]
    raise AssertionError(f"{where}: a `max_length` this test cannot read — {ast.dump(node)}")


def _bounds(module: str) -> dict[str, dict[str, int | None]]:
    """`{class: {field: max_length}}` for every text field ``module`` declares; ``None`` is a
    field without a bound, which only a `TextField` may be."""
    tree = ast.parse((APPS / module).read_text(encoding="utf-8"))
    constants = {
        statement.targets[0].id: statement.value.value
        for statement in tree.body
        if isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, int)
    }
    found: dict[str, dict[str, int | None]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        fields: dict[str, int | None] = {}
        for statement in node.body:
            if not (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Attribute)
                and statement.value.func.attr in TEXT_FIELDS
            ):
                continue
            name = statement.targets[0].id
            bound = next(
                (
                    _number(keyword.value, constants, f"{module} {node.name}.{name}")
                    for keyword in statement.value.keywords
                    if keyword.arg == "max_length"
                ),
                None,
            )
            fields[name] = bound
        found[node.name] = fields
    return found


def _accepted(source: tuple[str, str, str]) -> int | None:
    """How long a text Management accepts into ``source``; ``None`` for no bound at all."""
    if source == USERNAME:
        return USERNAME_LENGTH
    module, model, field = source
    fields = _bounds(module).get(model)
    assert fields is not None, f"no model `{model}` in {module} — has it moved?"
    assert field in fields, f"no text field `{model}.{field}` in {module} — has it moved?"
    return fields[field]


def _bounded_columns() -> list[tuple[str, str, int]]:
    """`(table, column, length)` for every length-bounded text column a mirrored table has.

    `Text` is a `String` without a length, which is what an unbounded column is.
    """
    columns = []
    for table in _mirrored_tables():
        for column in table.columns:
            if isinstance(column.type, String) and column.type.length is not None:
                columns.append((table.name, column.name, column.type.length))
    return columns


def _mirrored_tables() -> list:  # noqa: ANN201 — SQLAlchemy `Table`s
    """Every read-model table, and `api_keys`: the gateway's own table an event fills."""
    tables = [
        cls.__table__
        for _, cls in inspect.getmembers(read_models, inspect.isclass)
        if cls.__module__ == read_models.__name__ and hasattr(cls, "__table__")
    ]
    return [*tables, ApiKey.__table__]


def _source(table: str, column: str) -> tuple[str, str, str]:
    if (table, column) in SOURCES:
        return SOURCES[(table, column)]
    module, model = MIRRORS[table]
    return (module, model, column)


def test_every_table_an_event_writes_is_compared() -> None:
    """A new read-model must be mirrored here, or its columns are compared with nothing."""
    unmirrored = {table.name for table in _mirrored_tables()} - set(MIRRORS)

    assert unmirrored == set(), (
        f"{sorted(unmirrored)}: name the Management model each one mirrors in MIRRORS."
    )


def test_the_comparison_reads_what_it_claims_to() -> None:
    """Without this, a moved file or a renamed field turns the test below into a green nothing."""
    columns = {(table, column) for table, column, _ in _bounded_columns()}

    assert ("use_cases", "slug") in columns
    assert ("model_catalog", "display_name") in columns
    assert _accepted(SLUG) == 64
    assert _accepted(("usecases/models.py", "UseCase", "description")) == 20_000


def test_no_gateway_column_is_narrower_than_what_management_accepts() -> None:
    narrower = []
    for table, column, length in _bounded_columns():
        if (table, column) in NOT_FROM_MANAGEMENT:
            continue
        source = _source(table, column)
        accepted = _accepted(source)
        if accepted is None or accepted > length:
            field = ".".join(source[1:])
            said = "any length" if accepted is None else f"{accepted} characters"
            narrower.append(f"{table}.{column} holds {length}; {field} accepts {said}")

    assert narrower == [], (
        "The gateway would refuse the whole event for a value Management accepted, and the "
        "configuration would never reach the plane that enforces it:\n  "
        + "\n  ".join(narrower)
        + "\n\nWiden the column (a migration), or narrow Management's bound."
    )
