"""A request's technical record (`FRD-630`): where it ended, its milestones in milliseconds, what
reached the caller, and what it failed with — codes and numbers only.

Revision ID: 0058_request_technical_record
Revises: 0057_mail_notices
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0058_request_technical_record"
down_revision = "0057_mail_notices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("request_logs") as batch:
        batch.add_column(sa.Column("technical", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("request_logs") as batch:
        batch.drop_column("technical")
