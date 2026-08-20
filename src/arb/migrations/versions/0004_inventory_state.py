"""Inventory lifecycle state.

The timestamps on `inventory` already imply a state -- acquired but not listed, listed
but not sold -- but an implied state cannot be queried, counted or aged. As a column,
"what is stuck in transit" becomes a WHERE clause rather than a join over three
nullable dates, which is what makes an outstanding-tasks view possible at all.

Existing rows are backfilled from those same timestamps rather than defaulted to
`scouted`, because defaulting would report every historical purchase as unbought.

Revision ID: 0004_inventory_state
Revises: 0003_taxonomy_aspects
Create Date: 2026-08-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_inventory_state"
down_revision: str | Sequence[str] | None = "0003_taxonomy_aspects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("inventory", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("state", sa.String(), server_default="scouted", nullable=False)
        )
        batch_op.create_index("idx_inventory_state", ["state", "acquired_at"], unique=False)

    # Backfill from the timestamps that previously implied the state. Ordered
    # most-advanced first so a sold row is not overwritten by the listed rule.
    op.execute(sa.text("UPDATE inventory SET state = 'sold' WHERE sold_at IS NOT NULL"))
    op.execute(
        sa.text(
            "UPDATE inventory SET state = 'listed' WHERE sold_at IS NULL AND listed_at IS NOT NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE inventory SET state = 'in_transit' WHERE sold_at IS NULL AND listed_at IS NULL"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("inventory", schema=None) as batch_op:
        batch_op.drop_index("idx_inventory_state")
        batch_op.drop_column("state")
