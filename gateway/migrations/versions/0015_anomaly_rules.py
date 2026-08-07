"""What this installation considers abnormal.

Revision ID: 0015_anomaly_rules
Revises: 0014_upstream_provenance

FRD-500. The gateway's read-model of the rules authored in Management: what to watch, over what
window, above what threshold, and what to do then.

``use_case`` is **nullable on purpose** — NULL means the rule applies everywhere. Indexed together
with ``kind`` because the engine's every query is "the rules of kind K that apply to use case U",
which with a nullable column means U *or* NULL, and that is two index lookups rather than a scan.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_anomaly_rules"
down_revision = "0014_upstream_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "anomaly_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("use_case", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("window_minutes", sa.Integer(), nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("min_sample", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("action", sa.String(length=16), nullable=False, server_default="alert"),
        sa.Column("target", sa.String(length=16), nullable=False, server_default="subject"),
        sa.Column("action_minutes", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_anomaly_rules_use_case", "anomaly_rules", ["use_case"])
    op.create_index("ix_anomaly_rules_kind", "anomaly_rules", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_anomaly_rules_kind", table_name="anomaly_rules")
    op.drop_index("ix_anomaly_rules_use_case", table_name="anomaly_rules")
    op.drop_table("anomaly_rules")
