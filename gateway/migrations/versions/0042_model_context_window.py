"""What a model can hold at once, so a client can say how full the conversation is (FRD-132 §11).

Revision ID: 0042_model_context_window
Revises: 0041_retention_runs
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0042_model_context_window"
down_revision = "0041_retention_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Nullable, and nothing backfilled.

    A context window is a fact about a vendor's model that this installation has never recorded,
    so there is no value to derive one from. Guessing — 32k because most models are around there —
    would put a number a client sizes its conversation against into a column nobody measured. Empty
    means unknown, and unknown is not published at all.
    """
    op.add_column("model_catalog", sa.Column("context_window", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_catalog", "context_window")
