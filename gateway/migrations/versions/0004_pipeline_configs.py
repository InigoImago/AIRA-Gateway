"""gateway read-model: pipeline_configs (FRD-300)

Revision ID: 0004_pipeline_configs
Revises: 0003_api_key_use_case
Create Date: 2026-08-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_pipeline_configs"
down_revision = "0003_api_key_use_case"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_configs",
        sa.Column("use_case", sa.String(length=64), primary_key=True),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("fallback_models", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("pipeline_configs")
