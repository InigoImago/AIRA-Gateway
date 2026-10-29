"""Mail when a budget runs low or an anomaly rule fires (`FRD-409`): the use case's two settings,
and the table of mails owed.

The two columns are what an event from an older Management leaves at their defaults: NULL
thresholds read as `[100]` (`aira_common.mail`), and mail about findings on.

Revision ID: 0057_mail_notices
Revises: 0056_fallback_chains
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0057_mail_notices"
down_revision = "0056_fallback_chains"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("use_cases") as batch:
        batch.add_column(sa.Column("budget_mail_thresholds", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column(
                "mail_admins_on_anomalies",
                sa.Boolean(),
                server_default=sa.true(),
                nullable=False,
            )
        )
    op.create_table(
        "mail_notices",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("use_case", sa.String(length=64), nullable=False),
        sa.Column("subject_key", sa.String(length=320), nullable=False),
        sa.Column("period_key", sa.String(length=10), nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("recipients", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=255), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "kind", "subject_key", "period_key", "threshold", name="uq_mail_notice_once"
        ),
    )
    op.create_index("ix_mail_notices_created_at", "mail_notices", ["created_at"])
    op.create_index("ix_mail_notices_use_case", "mail_notices", ["use_case"])
    op.create_index("ix_mail_notices_expires_at", "mail_notices", ["expires_at"])
    op.create_index("ix_mail_notices_outcome", "mail_notices", ["outcome"])


def downgrade() -> None:
    op.drop_table("mail_notices")
    with op.batch_alter_table("use_cases") as batch:
        batch.drop_column("mail_admins_on_anomalies")
        batch.drop_column("budget_mail_thresholds")
