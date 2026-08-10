"""Cached and cache-write input recorded and priced apart (`FRD-133` stage A).

Three input token counts, not one. A cache read costs 0.1x base input on Anthropic and a write
1.25x (five minutes) or 2x (one hour); Azure discounts reads and charges some writes; Gemini passes
its saving on silently. Folding them into `prompt_tokens` — which is what happened until now, the
Anthropic mapping literally summing `input + cached + created` — prices a read ten times too high
and a write a quarter too low. A cost control that is wrong in the expensive direction is worse
than one that is absent.

`prompt_tokens` keeps its meaning: **all** input the request was charged for. The two new columns
are subsets of it, so every existing report, budget and index carries on saying what it said.

The price columns are nullable for the reason `FRD-403` gives about prices generally: a model
nobody has priced is not a free one. Absent here means "priced at the ordinary input rate", which
never under-bills, and the console says which models are missing the cheaper figure.

Revision ID: 0025_cache_tokens
Revises: 0024_model_approved
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_cache_tokens"
down_revision = "0024_model_approved"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("request_logs", sa.Column("cached_input_tokens", sa.Integer(), nullable=True))
    op.add_column("request_logs", sa.Column("cache_write_tokens", sa.Integer(), nullable=True))
    op.add_column(
        "model_catalog",
        sa.Column("cached_input_price_per_million_nanos", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "model_catalog",
        sa.Column("cache_write_price_per_million_nanos", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("model_catalog", "cache_write_price_per_million_nanos")
    op.drop_column("model_catalog", "cached_input_price_per_million_nanos")
    op.drop_column("request_logs", "cache_write_tokens")
    op.drop_column("request_logs", "cached_input_tokens")
