"""Access granted to a Keycloak group.

Revision ID: 0018_use_case_groups
Revises: 0017_access_suspensions

FRD-209. Membership had two answers that disagreed: the gateway derived it from Keycloak groups
named `/use-cases/<slug>`, Management from its own rows. A use case created in the console produced
the second and not the first, so its own administrator was — correctly — told the identity provider
did not consider them a member.

A grant binds a **principal** to a use case, and a principal is a group or a person. This table is
the group half, arriving over Kafka like every other piece of configuration the gateway reads
(`FRD-204`): the request path never asks Management anything.

The path is whatever the realm uses — `/ai/kundenservice`, not a naming convention AIRA imposes.
The old convention still resolves, from the token alone, as one route among several.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_use_case_groups"
down_revision = "0017_access_suspensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "use_case_groups",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("use_case_slug", sa.String(length=64), nullable=False, index=True),
        sa.Column("group_path", sa.String(length=255), nullable=False, index=True),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="user"),
        # One grant per (use case, group). A second row for the same pair is not a second grant,
        # it is the same grant with two roles — and an access decision that depends on which row
        # is read first is not a decision anybody can review.
        sa.UniqueConstraint("use_case_slug", "group_path", name="uq_group_grant"),
    )


def downgrade() -> None:
    op.drop_table("use_case_groups")
