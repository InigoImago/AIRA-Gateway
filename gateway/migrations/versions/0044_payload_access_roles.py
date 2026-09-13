"""Keep the reader's organisation-wide roles with each content read (`FRD-622` FR-3).

`ground` says on what authority content was read, and `incident` does not say whether a Global
Administrator or IT Security read it. A role held today is not evidence of the role held then, so
the roles are recorded with the read. Nullable: a read recorded before this has no snapshot, and
the log shows that as unknown rather than as "no roles".

Revision ID: 0044_payload_access_roles
Revises: 0043_payload_access_username
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0044_payload_access_roles"
down_revision = "0043_payload_access_username"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payload_access", sa.Column("roles", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("payload_access", "roles")
