"""The roles read model: what each stored role may do and which group confers it (`FRD-614`).

Only stored roles arrive here — IT Steuerung and the roles an installation defines. The Global
Administrator and IT Security are fixed in code and have no row.

Revision ID: 0045_roles
Revises: 0044_payload_access_roles
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0045_roles"
down_revision = "0044_payload_access_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("slug", sa.String(length=64), primary_key=True),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("group_path", sa.String(length=255), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("builtin", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("roles")
