"""How much of a response the model spent thinking (`FRD-135`).

NULL on every existing row, and that is the point: zero would claim the model did not think, and on
the rows this column was added for it did — one measured request against `gemini-2.5-flash` spent
143 of its 169 tokens reasoning, none of which any figure in this database knew about.

Revision ID: 0037_reasoning_tokens
Revises: 0036_log_issuer
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037_reasoning_tokens"
down_revision = "0036_log_issuer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("request_logs", sa.Column("reasoning_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("request_logs", "reasoning_tokens")
