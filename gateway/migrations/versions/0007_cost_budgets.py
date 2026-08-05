"""Cost-based budgeting: model prices, per-request cost, cost counters (FRD-403).

Money is stored as integer nano-units of the installation currency rather than NUMERIC: exact,
comparable, and identical on Postgres and the SQLite the tests run on.

Revision ID: 0007_cost_budgets
Revises: 0006_budget_usage
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_cost_budgets"
down_revision = "0006_budget_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_prices",
        sa.Column("model", sa.String(length=128), primary_key=True),
        sa.Column("display_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("input_price_per_million_nanos", sa.BigInteger(), nullable=True),
        sa.Column("output_price_per_million_nanos", sa.BigInteger(), nullable=True),
    )

    op.add_column("budgets", sa.Column("limit_cost_nanos", sa.BigInteger(), nullable=True))

    op.add_column(
        "budget_usage",
        sa.Column("cost_nanos", sa.BigInteger(), nullable=False, server_default="0"),
    )
    # Requests served by a model with no price on file: counted apart so a spend figure never
    # silently reads consumption as free.
    op.add_column(
        "budget_usage",
        sa.Column("unpriced_requests", sa.Integer(), nullable=False, server_default="0"),
    )

    op.add_column("request_logs", sa.Column("cost_nanos", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("request_logs", "cost_nanos")
    op.drop_column("budget_usage", "unpriced_requests")
    op.drop_column("budget_usage", "cost_nanos")
    op.drop_column("budgets", "limit_cost_nanos")
    op.drop_table("model_prices")
