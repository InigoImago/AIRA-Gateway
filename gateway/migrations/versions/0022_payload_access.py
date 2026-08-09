"""Reading a stored prompt is itself an event (`FRD-505`).

Two things arrive together because they are two halves of one decision. `ADR-0009` refused to show
individual requests to people outside the use case that produced them; the owner granted it on
2026-08-09 for the two incident roles, and what makes that reviewable afterwards is that the act of
reading leaves a row. Without `payload_access` the permission would be unauditable, which is the
condition the ADR was written to prevent.

`restrict_members_to_own_requests` is the other half: a use-case administrator can decide that the
people *inside* their use case see only what they themselves sent. Default **false** — that is the
behaviour that already existed, and a default flipped on shipping day silently narrows every use
case that was working yesterday.

Revision ID: 0022_payload_access
Revises: 0021_request_log_tool_calls

The id is short because Alembic stores it in a `varchar(32)`. The first draft was called
`0022_payload_access_and_member_restriction` — 42 characters — and the migration applied its DDL
and then failed writing its own version row, which rolls the whole thing back and leaves a stack
trace naming `alembic_version` rather than the migration. A length only a real database enforces:
the same shape as the Keycloak client description that broke a realm import at `varchar(255)`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_payload_access"
down_revision = "0021_request_log_tool_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "use_cases",
        sa.Column(
            "restrict_members_to_own_requests",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_table(
        "payload_access",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        # No foreign key on purpose: retention deletes the request row, and an access record that
        # vanished with the content it recorded would be exactly the wrong way round.
        sa.Column("request_log_id", sa.String(length=36), nullable=False),
        sa.Column("use_case", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("ground", sa.String(length=32), nullable=False, server_default=""),
    )
    op.create_index("ix_payload_access_created_at", "payload_access", ["created_at"])
    op.create_index("ix_payload_access_request_log_id", "payload_access", ["request_log_id"])
    op.create_index("ix_payload_access_subject", "payload_access", ["subject"])
    op.create_index("ix_payload_access_use_case", "payload_access", ["use_case"])


def downgrade() -> None:
    op.drop_index("ix_payload_access_use_case", table_name="payload_access")
    op.drop_index("ix_payload_access_subject", table_name="payload_access")
    op.drop_index("ix_payload_access_request_log_id", table_name="payload_access")
    op.drop_index("ix_payload_access_created_at", table_name="payload_access")
    op.drop_table("payload_access")
    op.drop_column("use_cases", "restrict_members_to_own_requests")
