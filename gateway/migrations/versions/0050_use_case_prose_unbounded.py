"""A use case's description and processing notes hold whatever Management accepts.

Management bounds both at `MAX_PROSE_LENGTH` characters; these columns were 2000. A longer text made
PostgreSQL refuse the whole `usecase.upserted` event, so the use case never reached the read-model —
and each later event for it carried the same text and was refused too, its retirement among them.
Unbounded here, so the bound has one owner.

Batch mode, so SQLite — which has no `ALTER COLUMN … TYPE` — takes the same revision.

Revision ID: 0050_use_case_prose_unbounded
Revises: 0049_how_usage_was_established
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0050_use_case_prose_unbounded"
down_revision = "0049_how_usage_was_established"
branch_labels = None
depends_on = None

#: The two prose columns, both written from the same event.
PROSE = ("description", "processing_notes")


def upgrade() -> None:
    with op.batch_alter_table("use_cases") as batch:
        for column in PROSE:
            batch.alter_column(
                column,
                existing_type=sa.String(length=2000),
                type_=sa.Text(),
                existing_nullable=False,
            )


def downgrade() -> None:
    with op.batch_alter_table("use_cases") as batch:
        for column in PROSE:
            batch.alter_column(
                column,
                existing_type=sa.Text(),
                type_=sa.String(length=2000),
                existing_nullable=False,
            )
