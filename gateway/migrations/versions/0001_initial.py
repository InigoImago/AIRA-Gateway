"""initial gateway schema: api_keys + request_logs

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("prefix", sa.String(length=32), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_prefix", "api_keys", ["prefix"], unique=True)

    op.create_table(
        "request_logs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("auth_method", sa.String(length=32), nullable=False),
        sa.Column("use_case", sa.String(length=255), nullable=True),
        sa.Column("source_ip", sa.String(length=64), nullable=True),
        sa.Column("api", sa.String(length=32), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("trace_id", sa.String(length=32), nullable=True),
        sa.Column("request_payload", sa.JSON(), nullable=True),
        sa.Column("response_payload", sa.JSON(), nullable=True),
    )
    op.create_index("ix_request_logs_created_at", "request_logs", ["created_at"])
    op.create_index("ix_request_logs_subject", "request_logs", ["subject"])
    op.create_index("ix_request_logs_use_case", "request_logs", ["use_case"])
    op.create_index("ix_request_logs_model", "request_logs", ["model"])
    op.create_index("ix_request_logs_trace_id", "request_logs", ["trace_id"])


def downgrade() -> None:
    op.drop_table("request_logs")
    op.drop_table("api_keys")
