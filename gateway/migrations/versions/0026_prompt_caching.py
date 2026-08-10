"""A use case may mark its stable prefix as cacheable (`FRD-133` stage B).

Off by default, and the reason is stronger than least privilege: on Vertex the prompt cache is
isolated per **organisation**, not per workspace, and AIRA holds one credential per platform for
many use cases. A use case whose system prompt is itself confidential should not be opted in by
somebody else's cost decision.

Revision ID: 0026_prompt_caching
Revises: 0025_cache_tokens
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026_prompt_caching"
down_revision = "0025_cache_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "use_cases",
        sa.Column(
            "prompt_caching_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("use_cases", "prompt_caching_enabled")
