"""An audit row keeps the server and the region a request went to as they were named.

`request_logs.provider` and `.region` held 32 characters. A self-hosted server is named by the
installation (`AIRA_OPENAI_SERVERS`) and audited under that name, and a catalogue entry names
regions of up to 64 characters, so a longer name failed the INSERT on PostgreSQL and the request —
served and billed — had no row. The writer cuts every text of a row to its column, which keeps the
row; but a region cut short is one the register of processing activities reads as a region no
released model names, a transfer that did not happen, and two servers cut to one prefix are one
server in the trail. So both columns hold what the catalogue holds, 64, and only a longer name is
cut.

Widening a `varchar` changes PostgreSQL's catalogue only: no row is rewritten and the indexes stay.
Batch mode, so SQLite — which has no `ALTER COLUMN … TYPE` — takes the same revision.

Revision ID: 0060_wider_provenance
Revises: 0059_use_case_fallback_chains
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0060_wider_provenance"
down_revision = "0059_use_case_fallback_chains"
branch_labels = None
depends_on = None

#: The two columns that say where a request was processed.
PROVENANCE = ("provider", "region")


def upgrade() -> None:
    with op.batch_alter_table("request_logs") as batch:
        for column in PROVENANCE:
            batch.alter_column(
                column,
                existing_type=sa.String(length=32),
                type_=sa.String(length=64),
                existing_nullable=True,
            )


def downgrade() -> None:
    with op.batch_alter_table("request_logs") as batch:
        for column in PROVENANCE:
            batch.alter_column(
                column,
                existing_type=sa.String(length=64),
                type_=sa.String(length=32),
                existing_nullable=True,
            )
