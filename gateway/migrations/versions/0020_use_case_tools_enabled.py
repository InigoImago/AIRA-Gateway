"""A use case may declare functions for the model to call — off unless somebody says otherwise.

`FRD-131`. `server_default="false"` rather than a nullable column: every use case that exists when
this runs has *not* opted in, and a NULL that some reader treats as false and another as unknown is
the ambiguity this whole feature is meant to avoid.

Revision ID: 0020_use_case_tools_enabled
Revises: 0019_api_key_expiry
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_use_case_tools_enabled"
down_revision = "0019_api_key_expiry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "use_cases",
        sa.Column("tools_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("use_cases", "tools_enabled")
