"""gateway read-model: use_cases + use_case_members (FRD-204)

Revision ID: 0002_read_model
Revises: 0001_initial
Create Date: 2026-08-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_read_model"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "use_cases",
        sa.Column("slug", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column("processing_notes", sa.String(length=2000), nullable=False, server_default=""),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "use_case_members",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("use_case_slug", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="user"),
        sa.UniqueConstraint("use_case_slug", "subject", name="uq_member"),
    )
    op.create_index("ix_use_case_members_use_case_slug", "use_case_members", ["use_case_slug"])
    op.create_index("ix_use_case_members_subject", "use_case_members", ["subject"])


def downgrade() -> None:
    op.drop_table("use_case_members")
    op.drop_table("use_cases")
