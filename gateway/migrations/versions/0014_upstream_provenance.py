"""Where each request was actually processed.

Revision ID: 0014_upstream_provenance
Revises: 0013_model_capabilities

FRD-115 FR-10. Under a residency requirement, "the configuration says EU" is a claim and "this
request went to `eu`" is evidence. These three columns are what turn the first into the second,
per request rather than per deployment — and `FRD-601` can then break spend and volume down by
them, which is what makes an EU claim auditable rather than merely asserted.

``provider`` and ``region`` are indexed: those are the two an audit filters by. ``publisher``
answers "which vendor" and is read alongside a row rather than searched for.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_upstream_provenance"
down_revision = "0013_model_capabilities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("request_logs", sa.Column("provider", sa.String(length=32), nullable=True))
    op.add_column("request_logs", sa.Column("publisher", sa.String(length=32), nullable=True))
    op.add_column("request_logs", sa.Column("region", sa.String(length=32), nullable=True))
    op.create_index("ix_request_logs_provider", "request_logs", ["provider"])
    op.create_index("ix_request_logs_region", "request_logs", ["region"])


def downgrade() -> None:
    op.drop_index("ix_request_logs_region", table_name="request_logs")
    op.drop_index("ix_request_logs_provider", table_name="request_logs")
    for column in ("region", "publisher", "provider"):
        op.drop_column("request_logs", column)
