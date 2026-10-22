"""A model's sampling defaults, applied where a caller sets none (`FRD-146` FR-20).

Revision ID: 0054_default_sampling
Revises: 0053_fixed_sampling
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0054_default_sampling"
down_revision = "0053_fixed_sampling"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_catalog") as batch:
        batch.add_column(sa.Column("default_sampling", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("model_catalog") as batch:
        batch.drop_column("default_sampling")
