"""The name beside the subject on a payload read (`FRD-613`).

Revision ID: 0043_payload_access_username
Revises: 0042_model_context_window
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0043_payload_access_username"
down_revision = "0042_model_context_window"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Nullable, and nothing backfilled.

    `payload_access` is the record `ADR-0009` reopened this view on: reading somebody's stored
    prompt is allowed *because* every read is written down. It recorded `subject` alone, which the
    two credentials spell differently — a directory id when the reader signed in, their username
    when they used a key — so one person's reads were filed under two names and *"who has read
    this use case's prompts"* could not be answered without knowing which credential each row came
    from.

    The same pairing `request_logs` has carried since `FRD-606`, for the same reason and with the
    same rule: the subject is what the row is about, the name is what a person is known by, and
    neither replaces the other. NULL on every row written before this column existed, which is
    exactly what it means — nobody recorded a name, rather than the reader having none.
    """
    op.add_column("payload_access", sa.Column("username", sa.String(length=255), nullable=True))
    # Indexed like its neighbour: *"who has read this person's prompts"* is asked of the largest
    # evidence table this installation keeps, by whoever is answering for a disclosure.
    op.create_index("ix_payload_access_username", "payload_access", ["username"])


def downgrade() -> None:
    op.drop_index("ix_payload_access_username", table_name="payload_access")
    op.drop_column("payload_access", "username")
