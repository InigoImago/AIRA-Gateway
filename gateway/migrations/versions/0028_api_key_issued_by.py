"""Who created a credential, when that is not who answers for it (`FRD-604` FR-5).

`subject` is the **owner**: the identity every audit row carries, because a row describes what
called. For a team's shared credential that owner is a technical account, and the fact a shared key
otherwise destroys — which human created it — has nowhere to live. This is that column.

Nullable, and blank for every key issued before it existed as well as for every ordinary key, where
the two are the same person. A distinction nobody asked for should not appear on every row.

Revision ID: 0028_api_key_issued_by
Revises: 0027_prompt_cache_ttl
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028_api_key_issued_by"
down_revision = "0027_prompt_cache_ttl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("issued_by", sa.String(length=150), nullable=True))


def downgrade() -> None:
    op.drop_column("api_keys", "issued_by")
