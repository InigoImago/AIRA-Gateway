"""Reasoning is on for every use case unless an administrator turns it off (`FRD-135`).

Every row is switched on here as Management switches its own. Management also announces each use
case again; this makes the read model right before those events arrive. The server default
follows, so a row written without the column returns reasoning as well.

Revision ID: 0046_reasoning_on_by_default
Revises: 0045_roles
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0046_reasoning_on_by_default"
down_revision = "0045_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("use_cases", "include_reasoning", server_default=sa.true())
    op.execute("UPDATE use_cases SET include_reasoning = true")


def downgrade() -> None:
    op.alter_column("use_cases", "include_reasoning", server_default=sa.false())
