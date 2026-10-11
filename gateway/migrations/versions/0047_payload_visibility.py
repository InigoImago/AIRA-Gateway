"""Who may read a use case's stored content, as one value rather than a boolean (`FRD-622`).

The boolean could say *"members see only their own"* and could not say the third thing the owner
asked for: stored, and readable by **nobody inside the use case** — only by the installation roles
that may read any content. Three values now: `members`, `own`, `oversight`.

Backfilled from the boolean, because that is exactly what it meant: `true` → `own`,
`false` → `members`. Management announces every live use case again in the same release, so an
installation whose events arrive out of order converges either way.

The column is **not nullable and defaults to `members`** — the behaviour that existed. A NULL here
would be a third state nobody configured, and the one thing worse than a wrong default is a
default that means "unknown" on a column the read path trusts.

Revision ID: 0047_payload_visibility
Revises: 0046_reasoning_on_by_default
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0047_payload_visibility"
down_revision = "0046_reasoning_on_by_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "use_cases",
        sa.Column(
            "payload_visibility", sa.String(length=16), nullable=False, server_default="members"
        ),
    )
    # The restriction that was already configured, carried over before anything reads the column.
    op.execute(
        "UPDATE use_cases SET payload_visibility = 'own' "
        "WHERE restrict_members_to_own_requests = true"
    )
    # Batch mode: SQLite has no `ALTER TABLE ... DROP COLUMN` before 3.35 and the hermetic suite
    # runs on whatever the interpreter ships with.
    with op.batch_alter_table("use_cases") as batch:
        batch.drop_column("restrict_members_to_own_requests")


def downgrade() -> None:
    op.add_column(
        "use_cases",
        sa.Column(
            "restrict_members_to_own_requests",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute(
        "UPDATE use_cases SET restrict_members_to_own_requests = true "
        "WHERE payload_visibility <> 'members'"
    )
    with op.batch_alter_table("use_cases") as batch:
        batch.drop_column("payload_visibility")
