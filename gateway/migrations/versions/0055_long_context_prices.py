"""What a request costs whose prompt is longer than a stated length (`FRD-408`).

Revision ID: 0055_long_context_prices
Revises: 0054_default_sampling
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0055_long_context_prices"
down_revision = "0054_default_sampling"
branch_labels = None
depends_on = None

_PRICES = (
    "long_context_input_price_per_million_nanos",
    "long_context_output_price_per_million_nanos",
    "long_context_cached_input_price_per_million_nanos",
    "long_context_cache_write_price_per_million_nanos",
)


def upgrade() -> None:
    with op.batch_alter_table("model_catalog") as batch:
        batch.add_column(sa.Column("long_context_threshold", sa.Integer(), nullable=True))
        for column in _PRICES:
            batch.add_column(sa.Column(column, sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("model_catalog") as batch:
        for column in reversed(_PRICES):
            batch.drop_column(column)
        batch.drop_column("long_context_threshold")
