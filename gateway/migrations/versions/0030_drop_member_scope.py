"""Remove budgets and rate limits that name one person (2026-08-14).

The ``member`` scope is gone on the owner's decision, and `Scope.applying` no longer resolves it.
A row that survives that is **enforced by nothing and visible in nothing** — it sits in the
read-model, matches no caller, and the console has no option that could show it. This project has
found that shape often enough to know it is worse than either keeping the feature or removing it.

The read-model is fed by Kafka and is not authoritative, so this deletion is a cleanup rather than
a decision: Management deletes the same rows in its own migration, which is where the decision
lives. Doing it here as well means an installation whose relay has not run yet is not enforcing
something nobody can see in the meantime.

Deleted rather than converted to `each_member`: a cap somebody set for one person is not a cap for
everybody, and widening it would invent a governance decision nobody made.

Revision ID: 0030_drop_member
Revises: 0029_uc_allowed_models
"""

from __future__ import annotations

from alembic import op

revision = "0030_drop_member"
down_revision = "0029_uc_allowed_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM budgets WHERE scope = 'member'")
    op.execute("DELETE FROM rate_limits WHERE scope = 'member'")
    # The counters those budgets accumulated go too. A `budget_usage` row keyed to a scope nothing
    # resolves is unreachable — it can never be read again, and it would keep a deleted person's
    # spend on file past every retention clock this system has.
    op.execute("DELETE FROM budget_usage WHERE scope_key LIKE 'member:%'")


def downgrade() -> None:
    """Nothing to restore. A delete has no inverse, and a no-op that reports success would say a
    rollback happened."""
