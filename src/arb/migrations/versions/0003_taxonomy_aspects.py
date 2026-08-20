"""Cache eBay taxonomy aspect enums.

Required by the August 2026 apparel rules: Size and Condition must be present and
standard on new fashion listings, and non-compliant values are blocked, held, or
accepted-but-not-indexed. Validating locally before publish needs the per-category
allowed values, and fetching them per listing would be one API call per publish.

The raw payload is stored and parsed on read, mirroring `comps_cache`. Unlike that
table this one is refreshable -- eBay's enums are public -- so it is keyed uniquely
per marketplace and category and upserted rather than appended.

Revision ID: 0003_taxonomy_aspects
Revises: 0002_upper_bound
Create Date: 2026-08-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from arb.db import UtcDateTime

# revision identifiers, used by Alembic.
revision: str = "0003_taxonomy_aspects"
down_revision: str | Sequence[str] | None = "0002_upper_bound"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "taxonomy_aspects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("marketplace_id", sa.String(), nullable=False),
        sa.Column("category_id", sa.String(), nullable=False),
        sa.Column("category_tree_id", sa.String(), nullable=True),
        sa.Column("category_tree_version", sa.String(), nullable=True),
        sa.Column("payload", sa.String(), nullable=False),
        sa.Column("fetched_at", UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "marketplace_id", "category_id", name="uq_taxonomy_marketplace_category"
        ),
    )


def downgrade() -> None:
    op.drop_table("taxonomy_aspects")
