"""A request row says how its usage was established, where it is not the vendor's final report.

`counted` — the caller left a stream before the vendor reported anything, and the request and the
received answer were counted at the vendor's own endpoint; `partial` — the vendor's running total
when the caller left. NULL is the vendor's own last word, which every existing row is (`FRD-128`
FR-6).

Nullable and without a default, so PostgreSQL adds it to the request log — the largest table here —
without rewriting a row. Batch mode, so SQLite takes the same revision.

Revision ID: 0049_how_usage_was_established
Revises: 0048_wider_totals_and_readers
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0049_how_usage_was_established"
down_revision = "0048_wider_totals_and_readers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("request_logs") as batch:
        batch.add_column(sa.Column("usage_basis", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("request_logs") as batch:
        batch.drop_column("usage_basis")
