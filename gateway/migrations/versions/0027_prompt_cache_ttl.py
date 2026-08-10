"""How long the provider keeps a use case's cached prefix (`FRD-133`).

`5m` or `1h`, defaulting to the cheap one. An hour costs about 2x the ordinary input price to
write against roughly 1.25x for five minutes, so it pays for itself only where the gap between
turns regularly exceeds five minutes — a question about somebody's traffic, which is why it is a
setting rather than a constant.

Revision ID: 0027_prompt_cache_ttl
Revises: 0026_prompt_caching
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027_prompt_cache_ttl"
down_revision = "0026_prompt_caching"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "use_cases",
        sa.Column("prompt_cache_ttl", sa.String(length=4), nullable=False, server_default="5m"),
    )


def downgrade() -> None:
    op.drop_column("use_cases", "prompt_cache_ttl")
