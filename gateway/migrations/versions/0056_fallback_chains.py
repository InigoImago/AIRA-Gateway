"""The installation's fallback chains, and what a use case does with them (`FRD-310`).

Revision ID: 0056_fallback_chains
Revises: 0055_long_context_prices
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0056_fallback_chains"
down_revision = "0055_long_context_prices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fallback_chains",
        sa.Column("kind", sa.String(length=16), primary_key=True),
        sa.Column("models", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    with op.batch_alter_table("pipeline_configs") as batch:
        batch.add_column(sa.Column("fallback_mode", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("pipeline_configs") as batch:
        batch.drop_column("fallback_mode")
    op.drop_table("fallback_chains")
