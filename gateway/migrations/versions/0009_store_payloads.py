"""Per-use-case switch for storing payloads at all (FRD-404).

Revision ID: 0009_store_payloads
Revises: 0008_retention
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_store_payloads"
down_revision = "0008_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # True by default: an upgrade must not silently stop recording what an installation was
    # recording yesterday. Switching it off is a deliberate act per use case.
    op.add_column(
        "use_cases",
        sa.Column("store_payloads", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("use_cases", "store_payloads")
