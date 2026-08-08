"""API keys may state an end date.

A credential with no expiry is one that has to be inventoried rather than one that lapses. NULL —
which is every key issued before this migration, and the break-glass key — means never, because an
expiry that cannot be omitted is an expiry an operator sets to the year 3000.

Revision ID: 0019_api_key_expiry
Revises: 0018_use_case_groups
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_api_key_expiry"
down_revision = "0018_use_case_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("api_keys", "expires_at")
