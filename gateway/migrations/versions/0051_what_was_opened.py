"""An access record says what was opened: the content, or a request's details (`FRD-627` FR-16).

Opening a request's details — model, tokens, cost, latency, trace — is recorded as reading its
content is, so a reader's looking leaves the same trace whichever half they opened. One table for
both, told apart by `kind`; every row before this one recorded content.

Revision ID: 0051_what_was_opened
Revises: 0050_use_case_prose_unbounded
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0051_what_was_opened"
down_revision = "0050_use_case_prose_unbounded"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("payload_access") as batch:
        batch.add_column(
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="content")
        )
        batch.create_index("ix_payload_access_kind", ["kind"])


def downgrade() -> None:
    with op.batch_alter_table("payload_access") as batch:
        batch.drop_index("ix_payload_access_kind")
        batch.drop_column("kind")
