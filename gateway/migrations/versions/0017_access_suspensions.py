"""Written decisions to stop traffic.

Revision ID: 0017_access_suspensions
Revises: 0016_anomaly_events

FRD-503. A suspension is what turns a finding into a control: a target, an action, an expiry, an
author and a reason. The last three are what make it a *decision* rather than a side effect — an
automatic block with none of them is an outage with a good reason, and the first thing anyone asks
at 03:00 is who did this.

Rows are kept after they are lifted, which is why `lifted_at` is a column rather than a delete:
"this caller was blocked for two hours last Tuesday" is exactly what an incident review asks.

`anomaly_rules.throttle_rpm` arrives with it. An action that reduces a rate has to say to what —
the second time a declared setting turned out to be missing the figure it needs (`FRD-501` §4.4 was
the first), which is why `FRD-503` §4.4 names the pattern: an enum member is not a specification.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_access_suspensions"
down_revision = "0016_anomaly_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("anomaly_rules", sa.Column("throttle_rpm", sa.Integer(), nullable=True))

    op.create_table(
        "access_suspensions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("use_case", sa.String(length=64), nullable=True),
        sa.Column("target", sa.String(length=16), nullable=False),
        sa.Column("target_value", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("throttle_rpm", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("author", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("lifted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lifted_by", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_access_suspensions_created_at", "access_suspensions", ["created_at"])
    op.create_index("ix_access_suspensions_use_case", "access_suspensions", ["use_case"])
    op.create_index("ix_access_suspensions_target_value", "access_suspensions", ["target_value"])


def downgrade() -> None:
    op.drop_table("access_suspensions")
    op.drop_column("anomaly_rules", "throttle_rpm")
