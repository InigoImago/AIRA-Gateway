"""Erasure as evidence: one row per retention pass (FRD-608 §2.4).

Revision ID: 0041_retention_runs
Revises: 0040_use_case_tombstone
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0041_retention_runs"
down_revision = "0040_use_case_tombstone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """A new table and nothing backfilled, because there is nothing to backfill.

    `RetentionService` has reported `payloads_cleared` and `rows_deleted` on every pass since
    `FRD-404` and nothing read them: they went into a log line and out of reach. Every pass before
    this migration is therefore unrecorded, and stays that way — inventing rows for them would put
    figures in a register that nobody measured.
    """
    op.create_table(
        "retention_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payloads_cleared", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_deleted", sa.Integer(), nullable=False, server_default="0"),
    )
    # The register asks one question of this table — *what was the last pass* — and asks it on
    # every read of a governance screen. One row out of an ordered scan is a query that grows with
    # the table; with the index it does not.
    op.create_index("ix_retention_runs_ran_at", "retention_runs", ["ran_at"])


def downgrade() -> None:
    op.drop_index("ix_retention_runs_ran_at", table_name="retention_runs")
    op.drop_table("retention_runs")
