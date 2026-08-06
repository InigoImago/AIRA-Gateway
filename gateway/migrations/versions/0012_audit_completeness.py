"""Audit completeness: outcome, asked-vs-served, calling system, decisions, degradation.

Revision ID: 0012_audit_completeness
Revises: 0011_usecase_tombstones

FRD-122. Every column is nullable and additive, so existing rows stay valid and every query,
report and index that reads ``model`` keeps its meaning — ``model`` is still *what answered*.

``outcome`` and ``credential`` are indexed for the two questions they exist to answer: how often a
control fired (reporting groups by outcome), and what one credential did (incident response filters
by it). Backfilling ``outcome`` for existing rows is deliberately *not* attempted: those rows
predate the refusal recording, so every one of them was served, and asserting that from a status
code would invent a fact the row never carried.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_audit_completeness"
down_revision = "0011_usecase_tombstones"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("request_logs", sa.Column("credential", sa.String(length=64), nullable=True))
    op.add_column(
        "request_logs", sa.Column("requested_model", sa.String(length=128), nullable=True)
    )
    op.add_column("request_logs", sa.Column("model_selection", sa.String(length=32), nullable=True))
    op.add_column("request_logs", sa.Column("outcome", sa.String(length=32), nullable=True))
    op.add_column("request_logs", sa.Column("pipeline_decisions", sa.JSON(), nullable=True))
    op.add_column("request_logs", sa.Column("degraded", sa.JSON(), nullable=True))
    op.create_index("ix_request_logs_outcome", "request_logs", ["outcome"])
    op.create_index("ix_request_logs_credential", "request_logs", ["credential"])


def downgrade() -> None:
    op.drop_index("ix_request_logs_credential", table_name="request_logs")
    op.drop_index("ix_request_logs_outcome", table_name="request_logs")
    for column in (
        "degraded",
        "pipeline_decisions",
        "outcome",
        "model_selection",
        "requested_model",
        "credential",
    ):
        op.drop_column("request_logs", column)
