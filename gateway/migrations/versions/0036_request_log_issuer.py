"""Which Keycloak realm minted the token a request was decided on (`FRD-118`).

NULL everywhere until a second issuer is configured, and NULL forever for API keys and demo
traffic — a credential that no realm issued has no issuer, which is different from an unknown one.
Nullable rather than defaulted to the configured issuer: back-filling every existing row with
today's realm would be a claim about history nobody checked.

Revision ID: 0036_request_log_issuer
Revises: 0035_drop_pipeline_start
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036_log_issuer"
down_revision = "0035_drop_pipeline_start"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("request_logs", sa.Column("issuer", sa.String(length=255), nullable=True))
    op.create_index("ix_request_logs_issuer", "request_logs", ["issuer"])


def downgrade() -> None:
    op.drop_index("ix_request_logs_issuer", table_name="request_logs")
    op.drop_column("request_logs", "issuer")
