"""Findings, and the byte count one of them needed.

Revision ID: 0016_anomaly_events
Revises: 0015_anomaly_rules

FRD-501. Two changes that arrived together because the second was discovered by building the first.

``anomaly_events`` records what a rule found: the measurement, its threshold, and the sample it was
drawn from. A finding nobody can check is a finding nobody acts on, and the first question anyone
asks is "how bad, out of how many" — so the row answers it without a join.

``request_logs.request_bytes`` is what the ``payload_size`` kind measures against. The body-size
middleware was already counting bytes to enforce the ceiling (`FRD-122` §12), so recording the
count costs nothing new. It is nullable, and every row written before this migration has NULL:
those rows are excluded from **both** sides of the share, because counting an unknown size as small
would make old traffic look innocent.

``anomaly_rules.parameter`` is the second number a kind may need — today only ``payload_size``'s
byte figure. `FRD-501` §4.4 records how a schema that passed 18 tests and six mutations turned out
to be missing it: nothing had yet tried to *evaluate* a rule.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_anomaly_events"
down_revision = "0015_anomaly_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("anomaly_rules", sa.Column("parameter", sa.BigInteger(), nullable=True))
    op.add_column("request_logs", sa.Column("request_bytes", sa.Integer(), nullable=True))

    op.create_table(
        "anomaly_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("rule_name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("use_case", sa.String(length=64), nullable=True),
        sa.Column("target", sa.String(length=16), nullable=False),
        sa.Column("target_value", sa.String(length=255), nullable=False),
        sa.Column("observed", sa.Integer(), nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("sample", sa.Integer(), nullable=False),
        sa.Column("window_minutes", sa.Integer(), nullable=False),
        sa.Column("action_taken", sa.String(length=32), nullable=False, server_default="alert"),
        sa.Column("detail", sa.String(length=500), nullable=False, server_default=""),
    )
    # The three an incident is read by: when, whose, and what kind.
    op.create_index("ix_anomaly_events_created_at", "anomaly_events", ["created_at"])
    op.create_index("ix_anomaly_events_use_case", "anomaly_events", ["use_case"])
    op.create_index("ix_anomaly_events_kind", "anomaly_events", ["kind"])
    op.create_index("ix_anomaly_events_target_value", "anomaly_events", ["target_value"])
    op.create_index("ix_anomaly_events_rule_id", "anomaly_events", ["rule_id"])


def downgrade() -> None:
    op.drop_table("anomaly_events")
    op.drop_column("request_logs", "request_bytes")
    op.drop_column("anomaly_rules", "parameter")
