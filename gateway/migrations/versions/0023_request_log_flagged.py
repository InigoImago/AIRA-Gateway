"""What a pipeline step objected to (`FRD-505` FR-5).

A column rather than a query over `pipeline_decisions`, for a reason this repository has paid for
before: JSON containment is written differently on SQLite and Postgres, and the hermetic suite runs
on one while production runs on the other — a filter exercised against only one of them is a filter
tested on one of them. It is also the question an incident opens with, which makes it an index
rather than a scan.

Existing rows keep `false`. That is honest rather than convenient: nothing recorded the fact at the
time, and backfilling it from the decisions column would state a measurement that was never taken —
the same rule as "unpriced is counted apart, never as zero".

Revision ID: 0023_flagged
Revises: 0022_payload_access
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_flagged"
down_revision = "0022_payload_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "request_logs",
        sa.Column("flagged", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_request_logs_flagged", "request_logs", ["flagged"])


def downgrade() -> None:
    op.drop_index("ix_request_logs_flagged", table_name="request_logs")
    op.drop_column("request_logs", "flagged")
