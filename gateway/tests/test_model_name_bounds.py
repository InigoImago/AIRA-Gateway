"""A model name comes from the caller's URL, and two shapes of it reached Postgres and failed.

Found on 2026-08-11 by running the integration suite against a real stack. Both are invisible to
this suite's own database — **SQLite enforces neither NUL bytes nor column lengths** — which is the
trap this project has recorded twice already (a Keycloak client description over `varchar(255)`,
and a 42-character migration id that applied its DDL and then failed writing `alembic_version`).

    POST /v1beta/models/mock-1%00:generateContent      → psycopg.DataError → **500**
    POST /v1beta/models/aaa…(300 chars):generateContent → the refusal's audit row is **lost**

The first breaks the rule the 174-edge-case round established: a caller's mistake is answered with
an actionable status and never with our error. The second is worse, and quieter — the request was
correctly refused with a 404, and the row recording that refusal failed to insert, so `FRD-122`'s
*"the log records what was asked"* was broken by the very row meant to satisfy it, by anyone who
can send a request.

Two fixes in two places, because they are two problems:

- `catalog.is_lookupable` refuses the *lookup*: a name no column can hold cannot name a declared
  model, so there is nothing to ask the database. Answering before the query also stops the reply
  depending on which database is behind it.
- `persistence.service._fits` bounds the *row*: an oversized name costs the row's precision and
  never the row itself.

These cases run on SQLite like the rest of the suite, so they cannot reproduce the database error
— what they assert is the behaviour that makes it unreachable.
"""

from __future__ import annotations

import pytest

from aira_gateway.catalog import MAX_MODEL_NAME, is_lookupable
from aira_gateway.persistence.service import MODEL_COLUMN, OPERATION_COLUMN, _fits


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("mock-1\x00", id="nul-byte"),
        pytest.param("mock\x01-1", id="control-character"),
        pytest.param("mock-1\x7f", id="delete-character"),
        pytest.param("a" * (MAX_MODEL_NAME + 1), id="longer-than-the-column"),
        pytest.param("", id="empty"),
    ],
)
def test_a_name_no_column_could_hold_is_never_looked_up(name: str) -> None:
    assert not is_lookupable(name)


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("mock-1", id="ordinary"),
        # A colon is legal and load-bearing: `qwen3:0.6b` is why `split_resource` splits at the
        # **last** colon (`FRD-123`). A bound that refused it would take every local model out.
        pytest.param("qwen3:0.6b", id="colon"),
        pytest.param("publishers/anthropic/models/claude", id="slashes"),
        pytest.param("a" * MAX_MODEL_NAME, id="exactly-the-column-width"),
        pytest.param("modèle-1", id="non-ascii"),
    ],
)
def test_a_name_a_model_could_actually_have_is_looked_up(name: str) -> None:
    """The other half, and the one that matters more: a bound that refuses real names is an
    outage. `qwen3:0.6b` is the case that already cost this project a live defect."""
    assert is_lookupable(name)


def test_an_oversized_name_costs_precision_and_not_the_row() -> None:
    """The audit row survives, cut to what the column holds. The alternative — refusing to write
    it — is the defect: a request the trail does not have."""
    value = _fits("a" * 300)

    assert value is not None
    assert len(value) == MODEL_COLUMN
    assert value == "a" * MODEL_COLUMN


def test_a_name_that_fits_is_untouched() -> None:
    assert _fits("qwen3:0.6b") == "qwen3:0.6b"
    # `None` stays `None`: a column that was not set is different from one set to "".
    assert _fits(None) is None


def test_a_nul_byte_does_not_cost_the_row_either() -> None:
    """The half the first fix did not reach, and it had to be measured to be found.

    Refusing the *lookup* stopped the 500; the audit row still carried the NUL into
    `request_logs.model`, Postgres refused the INSERT, and the refusal stayed unrecorded. Verified
    against the running stack before and after: `request_log_write_failed` in the log, then a row
    with the name stored and the outcome `model_not_found`.
    """
    assert _fits("mock-1\x00") == "mock-1"
    assert _fits("a\x01b\x7fc") == "abc"


def test_control_characters_are_removed_before_the_cut_not_after() -> None:
    """Order matters: cutting first would count characters that are about to be dropped, so a
    name of 128 storable characters padded with NULs would arrive short."""
    padded = ("a" * MODEL_COLUMN) + "\x00" * 20

    assert _fits(padded) == "a" * MODEL_COLUMN


def test_the_method_is_bounded_to_its_own_narrower_column() -> None:
    """`operation` is `String(64)` and also comes out of the URL — `model:method` is split by the
    surface, so a caller chooses both halves. A bound applied to one and not the other is the
    same defect with a different column name."""
    assert _fits("x" * 200, OPERATION_COLUMN) == "x" * OPERATION_COLUMN
