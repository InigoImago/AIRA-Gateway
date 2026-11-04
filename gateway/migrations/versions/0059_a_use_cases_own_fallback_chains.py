"""A use case's own fallback chain for embeddings and for rankings (`FRD-310`, `ADR-0052`).

Additive: both columns are nullable, and NULL is read as an empty chain, so the release before
this one — which serves beside it while a rolling update replaces it — reads the table unchanged.
`fallback_chains` and `pipeline_configs.fallback_mode`, which that release reads and this one does
not, are dropped by the next release (`FRD-127` §5.4).

Revision ID: 0059_use_case_fallback_chains
Revises: 0058_request_technical_record
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0059_use_case_fallback_chains"
down_revision = "0058_request_technical_record"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("pipeline_configs") as batch:
        batch.add_column(sa.Column("embedding_fallback_models", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("ranking_fallback_models", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("pipeline_configs") as batch:
        batch.drop_column("ranking_fallback_models")
        batch.drop_column("embedding_fallback_models")
