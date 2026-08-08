"""What the model asked to have run (`FRD-131` FR-7).

Names and counts only. Arguments are caller content and belong under `store_payloads`, inside the
retention clock and behind `FRD-406`'s redaction — a metadata column no clock covers is the wrong
home for them.

Revision ID: 0021_request_log_tool_calls
Revises: 0020_use_case_tools_enabled
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_request_log_tool_calls"
down_revision = "0020_use_case_tools_enabled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("request_logs", sa.Column("tool_calls", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("request_logs", "tool_calls")
