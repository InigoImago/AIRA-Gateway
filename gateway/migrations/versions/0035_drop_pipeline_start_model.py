"""Drop the pipeline's `start_model` (owner's decision; reverses `0034`).

A use case releases several models on purpose. Naming one on the *pipeline* reads as "this is the
model this use case uses" and narrows, for the reader, a decision the release deliberately left
open. The question catalogue asks for its entry model when a run is started instead, from the
models already released to that use case — a property of the run rather than of the pipeline.

The column was distributed on `pipeline.upserted` and read by two callers, both of which stop
reading it in the same change: the catalogue, which now carries its own model, and the dry run,
which goes back to inferring one. That inference is documented as unreliable in
`_model_the_pipeline_is_about` and is unchanged from before `0034` — a known, bounded gap rather
than a new one.

Revision ID: 0035_drop_pipeline_start
Revises: 0034_pipeline_start
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035_drop_pipeline_start"
down_revision = "0034_pipeline_start"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("pipeline_configs", "start_model")


def downgrade() -> None:
    op.add_column(
        "pipeline_configs",
        sa.Column("start_model", sa.String(length=128), nullable=False, server_default=""),
    )
