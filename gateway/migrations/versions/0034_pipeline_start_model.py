"""Where a request enters a pipeline when the caller names no model (`ADR-0020`).

Empty for every existing pipeline, and empty is an answer rather than a gap: it means nobody
declared one. That matters because the reader is the **dry run**, whose
`_model_the_pipeline_is_about` has guessed three times and been wrong three times — the first
registered model, the first released one, the first released one that can generate — each reported
back as `effective_model`, where a builder takes it for a decision somebody made. A backfilled
guess here would be the fourth, written into the database.

Revision ID: 0034_pipeline_start
Revises: 0033_trace_indexes
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034_pipeline_start"
down_revision = "0033_trace_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipeline_configs",
        sa.Column("start_model", sa.String(length=128), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("pipeline_configs", "start_model")
