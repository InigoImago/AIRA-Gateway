"""Which models a use case has been released (`FRD-308`).

**Nullable, and the null is the point.** Empty means "somebody released nothing, so this use case
may call nothing"; null means "no event has told us yet". A migration that defaulted every existing
row to `[]` would stop all traffic the moment it ran — before Management had published a single
release — and the outage would look like the feature working.

So the column arrives empty of information, the consumer fills it from the next `usecase.upserted`,
and the gateway treats null as *not yet answered*. That is the same split `FRD-307` made for
`approved`: what an older sender omits must never read as a decision it did not make.

Revision ID: 0029_uc_allowed_models
Revises: 0028_api_key_issued_by
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Short on purpose: `alembic_version.version_num` is `varchar(32)`, and a 42-character id once
# applied its DDL and then failed writing the row that records it.
revision = "0029_uc_allowed_models"
down_revision = "0028_api_key_issued_by"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("use_cases", sa.Column("allowed_models", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("use_cases", "allowed_models")
