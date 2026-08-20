"""Columns for the active-listing sweep.

`days_to_sell` cannot come from eBay's sold endpoint -- it carries no listing-start
date. The route that does work is watching active listings: record when the venue says
each was created, notice when it disappears from search, and corroborate the
disappearance against a completed sale.

Two columns make that possible. `listings.venue_created_at` is the venue's own
creation timestamp, distinct from `first_seen` (when our scanner happened to look).
`sold_obs.external_id` is what lets a disappearance be matched to a real sale, because
a listing vanishing from search means it sold *or* was ended unsold and nothing else
distinguishes them.

Revision ID: 0005_sweep_columns
Revises: 0004_inventory_state
Create Date: 2026-08-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from arb.db import UtcDateTime

revision: str = "0005_sweep_columns"
down_revision: str | Sequence[str] | None = "0004_inventory_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("listings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("venue_created_at", UtcDateTime(), nullable=True))
    with op.batch_alter_table("sold_obs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("external_id", sa.String(), nullable=True))
        batch_op.create_index("idx_sold_external", ["external_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("sold_obs", schema=None) as batch_op:
        batch_op.drop_index("idx_sold_external")
        batch_op.drop_column("external_id")
    with op.batch_alter_table("listings", schema=None) as batch_op:
        batch_op.drop_column("venue_created_at")
