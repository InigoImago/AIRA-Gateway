"""Whether a model samples at its own settings only (`FRD-145`).

Anthropic's current Claude models refuse `top_k`, and `temperature` or `top_p` other than their
neutral values; the catalogue says so per model, and the dispatch chain reads it from this column.

Revision ID: 0053_fixed_sampling
Revises: 0052_the_callers_message
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0053_fixed_sampling"
down_revision = "0052_the_callers_message"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_catalog") as batch:
        batch.add_column(
            sa.Column("fixed_sampling", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("model_catalog") as batch:
        batch.drop_column("fixed_sampling")
