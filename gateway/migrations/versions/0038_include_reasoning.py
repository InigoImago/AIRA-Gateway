"""Whether a use case returns and keeps a model's reasoning (`FRD-135`).

Default **false**, and back-filled false rather than left NULL: every existing use case has already
decided, by never having been asked, that it does not want reasoning stored. NULL would mean
"unknown" about a content-retention question, and the safe reading of unknown is the same as false
— so saying false out loud is both correct and clearer than a nullable column nobody can interpret.

Revision ID: 0038_include_reasoning
Revises: 0037_reasoning_tokens
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0038_include_reasoning"
down_revision = "0037_reasoning_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "use_cases",
        sa.Column("include_reasoning", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("use_cases", "include_reasoning")
