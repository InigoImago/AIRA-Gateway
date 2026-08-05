"""Clear what earlier use-case deletions left behind in the read-model.

Revision ID: 0011_usecase_tombstones
Revises: 0010_rate_limits
"""

from __future__ import annotations

from alembic import op

revision = "0011_usecase_tombstones"
down_revision = "0010_rate_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Deactivate and remove the rows whose use case no longer exists.

    Until now ``usecase.deleted`` removed only the use case and its members, so keys, budgets,
    limits, pipelines and usage counters were left pointing at nothing. The keys are the part
    that matters: they kept authenticating, so an installation that deleted a use case still had
    live access it believed it had withdrawn. Existing installations do not get a second
    ``usecase.deleted`` event to fix this — the cascade in the consumer only helps from here on —
    so the rows already orphaned are cleared once, here.

    Keys are deactivated rather than deleted, matching the consumer: revocation is terminal, and
    a row that is gone could be recreated by a replayed ``api_key.created``.
    """
    op.execute(
        """
        UPDATE api_keys SET is_active = false
         WHERE use_case IS NOT NULL
           AND use_case NOT IN (SELECT slug FROM use_cases)
        """
    )
    for table in ("budgets", "rate_limits", "pipeline_configs"):
        op.execute(f"DELETE FROM {table} WHERE use_case NOT IN (SELECT slug FROM use_cases)")
    op.execute(
        """
        DELETE FROM budget_usage
         WHERE (scope_key LIKE 'uc:%%'
                AND substring(scope_key from 4) NOT IN (SELECT slug FROM use_cases))
            OR (scope_key LIKE 'member:%%'
                AND split_part(scope_key, ':', 2) NOT IN (SELECT slug FROM use_cases))
        """
    )
    # request_logs are deliberately untouched: the audit trail and the spend history outlive the
    # use case, and their payloads already expire on the retention clock (FRD-404).


def downgrade() -> None:
    """Nothing to undo: this removes rows that referenced a use case that no longer exists, and
    reinstating them would reinstate access that was meant to be gone."""
