"""gateway: budget_usage accounting (FRD-401)

Revision ID: 0006_budget_usage
Revises: 0005_budgets
Create Date: 2026-08-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_budget_usage"
down_revision = "0005_budgets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budget_usage",
        sa.Column("scope_key", sa.String(length=320), primary_key=True),
        sa.Column("period_key", sa.String(length=10), primary_key=True),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("budget_usage")
