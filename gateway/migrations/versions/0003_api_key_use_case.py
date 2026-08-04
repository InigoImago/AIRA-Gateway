"""api_keys.use_case for Management-issued key binding (FRD-205)

Revision ID: 0003_api_key_use_case
Revises: 0002_read_model
Create Date: 2026-08-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_api_key_use_case"
down_revision = "0002_read_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("use_case", sa.String(length=64), nullable=True))
    op.create_index("ix_api_keys_use_case", "api_keys", ["use_case"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_use_case", table_name="api_keys")
    op.drop_column("api_keys", "use_case")
