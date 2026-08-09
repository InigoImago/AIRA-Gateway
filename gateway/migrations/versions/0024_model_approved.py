"""Only an approved model may be used (`FRD-307`).

Default **true** in the read-model and **false** in Management. Management is where the decision is
made, so a new declaration starts unapproved there; this table is fed by events, and an event from
an older Management carries no such field. Reading its absence as "not approved" would take every
model out of service the moment one plane is upgraded before the other.

Revision ID: 0024_model_approved
Revises: 0023_flagged
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_model_approved"
down_revision = "0023_flagged"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_catalog",
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("model_catalog", "approved")
