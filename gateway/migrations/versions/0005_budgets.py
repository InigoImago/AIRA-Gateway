"""gateway read-model: budgets (FRD-400)

Revision ID: 0005_budgets
Revises: 0004_pipeline_configs
Create Date: 2026-08-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_budgets"
down_revision = "0004_pipeline_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budgets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("use_case", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("period", sa.String(length=8), nullable=False),
        sa.Column("limit_tokens", sa.Integer(), nullable=True),
        sa.Column("limit_requests", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_budgets_use_case", "budgets", ["use_case"])


def downgrade() -> None:
    op.drop_index("ix_budgets_use_case", table_name="budgets")
    op.drop_table("budgets")
