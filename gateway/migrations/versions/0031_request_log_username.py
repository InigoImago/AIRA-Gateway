"""The name a subject was known by, so one person is one figure (`FRD-606`).

**Descriptive, never an identity.** `subject` stays what a row is about and what every counter and
every enforcement decision is keyed on. This column exists because the two credentials answer "who
is this" in different alphabets — an OIDC token's subject is the directory's user id, an API key's
is its owner's username — so one person was two rows in every per-member figure and nothing could
join them.

Nullable, and the null carries information: a row written before this column, or by a credential
that names nobody, is grouped as itself rather than folded into somebody. Backfilling would mean
inventing the name a subject *probably* had, which is the one thing an audit row must not do.

Revision ID: 0031_req_log_username
Revises: 0030_drop_member
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031_req_log_username"
down_revision = "0030_drop_member"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("request_logs", sa.Column("username", sa.String(length=255), nullable=True))
    op.create_index("ix_request_logs_username", "request_logs", ["username"])


def downgrade() -> None:
    op.drop_index("ix_request_logs_username", table_name="request_logs")
    op.drop_column("request_logs", "username")
