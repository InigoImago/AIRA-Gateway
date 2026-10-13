"""Running totals that hold a period, and content reads that say what read them.

- **`budget_usage` totals become 64-bit.** One request is bounded by `MAX_ACCOUNTABLE_TOKENS`, but
  a row adds up every request of a scope for a whole day or month, and a 32-bit total stops
  accepting bookings at about two billion tokens — after which every served request in that scope
  fails its booking. `requests` and `unpriced_requests` widen with it: the same row, the same
  arithmetic, and no reason for one of its counters to be the one that overflows.
- **`payload_access.roles` becomes unbounded.** A custom role's slug may be 64 characters, so a
  reader holding five of them does not fit 255, and a content read whose record does not fit is a
  read that does not happen.
- **`payload_access` records the method and credential that read** (`FRD-622` FR-6). An API key
  reads on its owner's grant; without these its reads are filed as the owner's own. Nullable: a
  read recorded before this does not say, which is not "a person at the console".

Batch mode, so SQLite — which has no `ALTER COLUMN … TYPE` — takes the same revision.

Revision ID: 0048_wider_totals_and_readers
Revises: 0047_payload_visibility
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0048_wider_totals_and_readers"
down_revision = "0047_payload_visibility"
branch_labels = None
depends_on = None

#: The `budget_usage` counters that widen; `cost_nanos` has been 64-bit since `0007`.
TOTALS = ("tokens", "requests", "unpriced_requests")


def upgrade() -> None:
    with op.batch_alter_table("budget_usage") as batch:
        for column in TOTALS:
            batch.alter_column(
                column,
                existing_type=sa.Integer(),
                type_=sa.BigInteger(),
                existing_nullable=False,
            )
    with op.batch_alter_table("payload_access") as batch:
        batch.alter_column(
            "roles",
            existing_type=sa.String(length=255),
            type_=sa.Text(),
            existing_nullable=True,
        )
        batch.add_column(sa.Column("auth_method", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("credential", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("payload_access") as batch:
        batch.drop_column("credential")
        batch.drop_column("auth_method")
        batch.alter_column(
            "roles",
            existing_type=sa.Text(),
            type_=sa.String(length=255),
            existing_nullable=True,
        )
    with op.batch_alter_table("budget_usage") as batch:
        for column in TOTALS:
            batch.alter_column(
                column,
                existing_type=sa.BigInteger(),
                type_=sa.Integer(),
                existing_nullable=False,
            )
