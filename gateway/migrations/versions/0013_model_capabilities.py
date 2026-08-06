"""Model capability metadata, and a table name that stops lying.

Revision ID: 0013_model_capabilities
Revises: 0012_audit_completeness

FRD-114. ``model_prices`` is renamed to ``model_catalog``: once the row decides whether a thinking
budget is accepted, calling it *prices* misleads whoever reads it next. The rename is done first so
the added columns land on the table under its true name.

Every new column is nullable or defaulted. An installation that has only ever set prices keeps
working — those models are simply **undeclared**, which the gateway reads as the baseline
capabilities and nothing more (FR-7). No backfill is attempted: inventing a capability set for a
model nobody has described would be exactly the permissive default that requirement rules out.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_model_capabilities"
down_revision = "0012_audit_completeness"
branch_labels = None
depends_on = None

_COLUMNS = (
    sa.Column("capabilities", sa.JSON(), nullable=True),
    sa.Column("publisher", sa.String(length=32), nullable=False, server_default=""),
    sa.Column("platform", sa.String(length=32), nullable=False, server_default=""),
    sa.Column("addressing", sa.JSON(), nullable=True),
    sa.Column("underlying_model", sa.String(length=128), nullable=False, server_default=""),
    sa.Column("max_output_tokens", sa.Integer(), nullable=True),
    sa.Column("default_max_output_tokens", sa.Integer(), nullable=True),
    sa.Column("thinking", sa.JSON(), nullable=True),
    sa.Column("embedding", sa.JSON(), nullable=True),
    sa.Column("attachments", sa.JSON(), nullable=True),
    sa.Column("hosting", sa.String(length=16), nullable=False, server_default=""),
    sa.Column("deprecated", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("numeric_id", sa.Integer(), nullable=True),
)


def upgrade() -> None:
    op.rename_table("model_prices", "model_catalog")
    for column in _COLUMNS:
        op.add_column("model_catalog", column)
    op.create_index("ix_model_catalog_numeric_id", "model_catalog", ["numeric_id"])


def downgrade() -> None:
    op.drop_index("ix_model_catalog_numeric_id", table_name="model_catalog")
    for column in reversed(_COLUMNS):
        op.drop_column("model_catalog", column.name)
    op.rename_table("model_catalog", "model_prices")
