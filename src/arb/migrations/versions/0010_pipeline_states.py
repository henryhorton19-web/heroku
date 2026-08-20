"""Complete Stockly's lifecycle states.

The roadmap adopted `Scouted -> Sniped -> In-Transit -> Enhanced -> Listed -> Sold`;
only four of the six were ever stored. The two missing ones are real stages -- `sniped`
is bought-but-not-yet-dispatched, `enhanced` is photographed and written but not yet
published -- and without them the pipeline view collapses distinct work into one bar.

No data migration is needed: the column is free text with a `scouted` default, and no
existing row can hold a value that is now invalid. Widening a vocabulary is safe in a
way narrowing it would not be.

The seventh stage the UI shows, `funds_cleared`, is deliberately NOT stored. It is
derived from `actual_fees_pence` being present, and storing it would create a state
requiring sync with a column that already answers the question.

Revision ID: 0010_pipeline_states
Revises: 0009_own_listings
Create Date: 2026-08-20

"""

from collections.abc import Sequence

revision: str = "0010_pipeline_states"
down_revision: str | Sequence[str] | None = "0009_own_listings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No schema change: `inventory.state` is free text and the vocabulary widened."""


def downgrade() -> None:
    """Nothing to undo. Rows written with the new states remain readable."""
