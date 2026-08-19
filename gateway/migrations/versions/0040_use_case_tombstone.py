"""A retired use case keeps its row (FRD-607).

Revision ID: 0040_use_case_tombstone
Revises: 0039_drop_superseded_index
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0040_use_case_tombstone"
down_revision = "0039_drop_superseded_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Nullable and unbackfilled: every row here is a use case that has not been retired.

    The rows this exists to keep are the ones already destroyed by the old hard delete, and those
    are not recoverable — the argument for shipping this rather than writing a note about it.
    """
    op.add_column("use_cases", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    # Indexed because retention and the payload reader both filter on it every time they run, and
    # because the set of retired use cases is small and the set of live ones is not — which is the
    # shape a partial index would suit if this ever grows enough to need one.
    op.create_index("ix_use_cases_deleted_at", "use_cases", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_use_cases_deleted_at", table_name="use_cases")
    op.drop_column("use_cases", "deleted_at")
