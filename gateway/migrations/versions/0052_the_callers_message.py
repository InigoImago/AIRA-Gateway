"""The message a caller was given is kept on its audit row (`FRD-628` FR-4).

Under strict data mode the prompt and the answer are not readable by the people who run a use case;
why a request failed has to be readable from its row instead, and the reason a refusal gave was in
the response and the log only.

Revision ID: 0052_the_callers_message
Revises: 0051_what_was_opened
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0052_the_callers_message"
down_revision = "0051_what_was_opened"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("request_logs") as batch:
        batch.add_column(sa.Column("error", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("request_logs") as batch:
        batch.drop_column("error")
