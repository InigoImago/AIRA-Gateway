"""The indexes the two queries somebody runs during an incident actually need (`FRD-502`/`FRD-505`).

Two gaps, found by reading the query beside the schema on 2026-08-15.

**`source_ip` had no index at all**, and `/v1beta/traces?source_ip=` filters on it. That is the
first question of an incident — *which machine is doing this* — asked against the largest table
this system has, and it was a sequential scan every time. The column is guarded by an incident
role, so the people it slows down are exactly the ones who cannot wait.

**The cursor page had no composite index.** `traces` filters `use_case IN (…)` and orders by
`(created_at DESC, id DESC)`; with single-column indexes Postgres can use one of the two and then
sorts. On a table that grows with every request, that turns page one of a use case's own history
into a sort of everything it ever did. `(use_case, created_at DESC, id DESC)` answers the filter
and the order together, which is what a keyset page is for.

`created_at DESC, id DESC` and not the ascending default: an index is usable in either direction,
but the descending pair also serves the exact ordering the endpoint asks for without a backwards
scan, and it costs nothing to say so.

Nothing is dropped and no column changes, so this is safe to apply to a live table — though on a
large one it is worth running the equivalent `CREATE INDEX CONCURRENTLY` by hand and stamping the
revision, which is why the index names here are the ones Postgres would use.

Revision ID: 0033_trace_indexes
Revises: 0032_merge_member
"""

from __future__ import annotations

from alembic import op

revision = "0033_trace_indexes"
down_revision = "0032_merge_member"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_request_logs_source_ip", "request_logs", ["source_ip"])
    op.create_index(
        "ix_request_logs_use_case_page",
        "request_logs",
        ["use_case", "created_at", "id"],
        postgresql_ops={"created_at": "DESC", "id": "DESC"},
    )


def downgrade() -> None:
    op.drop_index("ix_request_logs_use_case_page", table_name="request_logs")
    op.drop_index("ix_request_logs_source_ip", table_name="request_logs")
