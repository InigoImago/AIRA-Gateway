"""Drop the index `0033`'s paging index superseded, and left behind.

`0008` created `ix_request_logs_use_case_created_at` on `(use_case, created_at)`. `0033` created
`ix_request_logs_use_case_page` on `(use_case, created_at DESC, id DESC)` for the trace view's
keyset page — the same leading columns, one more of them — and did not drop the older one. Both
have been maintained on every insert since, on the table that takes **a row per request**.

Measured on the demo stack before writing this, because "probably redundant" is not a reason to
drop an index:

    ix_request_logs_use_case_page          11 706 scans
    ix_request_logs_use_case_created_at        29 scans

The 29 are queries the surviving index answers too: Postgres reads a DESC index backwards for an
ascending order and uses any leading-column prefix for a range. Nothing loses a plan.

**Found by a check that did not exist.** Management has had `makemigrations --check` since August;
the gateway's Alembic side had no equivalent, so a migration adding something no model declares had
nowhere to be noticed. `tests/integration/test_the_gateway_migrations_match_its_models.py` is that
check, and this is the first thing it reported.

Revision ID: 0039_drop_superseded_index
Revises: 0038_include_reasoning
"""

from __future__ import annotations

from alembic import op

revision = "0039_drop_superseded_index"
down_revision = "0038_include_reasoning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `IF EXISTS`, because a database built from the models rather than from the migrations never
    # had it: `create_all` follows `db/models.py`, which is where the index was missing from.
    op.execute("DROP INDEX IF EXISTS ix_request_logs_use_case_created_at")


def downgrade() -> None:
    op.create_index(
        "ix_request_logs_use_case_created_at", "request_logs", ["use_case", "created_at"]
    )
