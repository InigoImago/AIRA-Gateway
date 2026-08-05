"""Read-model for per-use-case request-rate limits (FRD-405).

Revision ID: 0010_rate_limits
Revises: 0009_store_payloads
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_rate_limits"
down_revision = "0009_store_payloads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No rows are created here on purpose. An installation that upgrades has no rate limits and
    # therefore stays unlimited, exactly as it was yesterday — a release must not begin refusing
    # traffic that used to be served (FRD-405 FR-8).
    op.create_table(
        "rate_limits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("use_case", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("limit_rpm", sa.Integer(), nullable=False),
        sa.Column("burst", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_rate_limits_use_case", "rate_limits", ["use_case"])


def downgrade() -> None:
    op.drop_index("ix_rate_limits_use_case", table_name="rate_limits")
    op.drop_table("rate_limits")
