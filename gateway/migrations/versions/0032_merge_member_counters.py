"""One person, one counter: fold subject-keyed usage into name-keyed usage (`ADR-0019`).

A per-head budget was keyed on the caller's **subject**, and the two credentials answer "who is
this" in different alphabets — an API key's subject already *is* its owner's username, an OIDC
token's is the directory's user id. So one human had two counters. The gateway now keys on the
person, and this brings the history with it: without it, everybody who signs in appears to start
the period from zero, which under-counts a budget in the one direction a budget must not be wrong.

**The mapping is observed, not invented.** `request_logs` carries `subject` and `username` side by
side (`FRD-606`), so a subject that has made a request since that column existed can be resolved to
the name it was known by. A subject with no such row is left exactly where it is: it keys nobody
now, which is harmless, and guessing the name it *probably* had is the one thing an audit trail
must not do.

Where both counters exist for one person and period — the ordinary case, a key and a sign-in — the
two rows are **summed**. That is the whole point: the pots merge, carrying what each had spent.

Revision ID: 0032_merge_member
Revises: 0031_req_log_username
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032_merge_member"
down_revision = "0031_req_log_username"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    # One name per subject: the most recent one it was seen under. A subject whose name changed
    # keeps its current one, which is the same answer the running gateway would give.
    mapping = connection.execute(
        sa.text(
            """
            SELECT DISTINCT ON (subject) subject, username
            FROM request_logs
            WHERE username IS NOT NULL AND username <> subject
            ORDER BY subject, created_at DESC
            """
        )
    ).all()

    for subject, username in mapping:
        rows = connection.execute(
            sa.text(
                """
                SELECT scope_key, period_key, tokens, requests, cost_nanos, unpriced_requests
                FROM budget_usage
                WHERE scope_key LIKE :pattern
                """
            ),
            {"pattern": f"member:%:{subject}"},
        ).all()
        for scope_key, period_key, tokens, requests, cost_nanos, unpriced in rows:
            # `member:<slug>:<subject>` — the slug is everything between the two fixed parts, and a
            # slug cannot contain a colon (`is_valid_use_case`), so this split is exact.
            slug = scope_key[len("member:") : -(len(subject) + 1)]
            merged = f"member:{slug}:{username}"
            connection.execute(
                sa.text(
                    """
                    INSERT INTO budget_usage
                        (scope_key, period_key, tokens, requests, cost_nanos, unpriced_requests)
                    VALUES (:key, :period, :tokens, :requests, :cost, :unpriced)
                    ON CONFLICT (scope_key, period_key) DO UPDATE SET
                        tokens = budget_usage.tokens + EXCLUDED.tokens,
                        requests = budget_usage.requests + EXCLUDED.requests,
                        cost_nanos = budget_usage.cost_nanos + EXCLUDED.cost_nanos,
                        unpriced_requests =
                            budget_usage.unpriced_requests + EXCLUDED.unpriced_requests
                    """
                ),
                {
                    "key": merged,
                    "period": period_key,
                    "tokens": tokens,
                    "requests": requests,
                    "cost": cost_nanos,
                    "unpriced": unpriced,
                },
            )
            connection.execute(
                sa.text("DELETE FROM budget_usage WHERE scope_key = :key AND period_key = :period"),
                {"key": scope_key, "period": period_key},
            )


def downgrade() -> None:
    """Deliberately empty.

    Splitting a merged counter back in two would need the share each credential contributed, and
    that is precisely the information the merge threw away. Leaving the rows folded is the honest
    answer: a downgraded gateway reads one pot per person, which over-counts nobody.
    """
