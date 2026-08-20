"""Our own listings across venues, and the de-listing hazard they track.

Selling the same item twice costs a refund, a defect, and sometimes the account. That
risk arrives with the *second* sell venue, so this lands before any second adapter.

Cross-venue de-listing is a distributed operation over systems that fail
independently. Intent is therefore recorded before the API call: `delist_requested_at`
when we decide a listing must come down, `delisted_at` only when a venue confirms. A
row with the first and not the second is an open hazard someone must resolve.

Revision ID: 0009_own_listings
Revises: 0008_synthetic_flag
Create Date: 2026-08-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from arb.db import UtcDateTime

revision: str = "0009_own_listings"
down_revision: str | Sequence[str] | None = "0008_synthetic_flag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "own_listings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("inventory_id", sa.Integer(), nullable=False),
        sa.Column("venue", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("ask_pence", sa.Integer(), nullable=False),
        sa.Column("listed_at", UtcDateTime(), nullable=False),
        sa.Column("sold_at", UtcDateTime(), nullable=True),
        sa.Column("delist_requested_at", UtcDateTime(), nullable=True),
        sa.Column("delisted_at", UtcDateTime(), nullable=True),
        sa.Column("delist_error", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["inventory_id"], ["inventory.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("venue", "external_id", name="uq_own_venue_external"),
    )
    op.create_index("idx_own_inventory", "own_listings", ["inventory_id"])


def downgrade() -> None:
    op.drop_index("idx_own_inventory", table_name="own_listings")
    op.drop_table("own_listings")
