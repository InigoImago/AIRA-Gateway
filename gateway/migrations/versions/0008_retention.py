"""Per-use-case payload retention (FRD-404).

Revision ID: 0008_retention
Revises: 0007_cost_budgets
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_retention"
down_revision = "0007_cost_budgets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Seven days by default, so an installation that upgrades without touching anything starts
    # deleting old prompts rather than keeping them forever.
    op.add_column(
        "use_cases",
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default="7"),
    )
    # The pruner scans by (use_case, created_at); without this it is a sequential scan over a
    # table that only ever grows.
    op.create_index(
        "ix_request_logs_use_case_created_at", "request_logs", ["use_case", "created_at"]
    )
    # Rows written before this change stored an absent payload as the JSON value `null` rather
    # than SQL NULL, which the pruner cannot tell apart from a payload it still has to clear.
    op.execute(
        "UPDATE request_logs SET request_payload = NULL WHERE request_payload::text = 'null'"
    )
    op.execute(
        "UPDATE request_logs SET response_payload = NULL WHERE response_payload::text = 'null'"
    )


def downgrade() -> None:
    op.drop_index("ix_request_logs_use_case_created_at", table_name="request_logs")
    op.drop_column("use_cases", "retention_days")
