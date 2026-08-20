"""Mark seeded inventory as synthetic.

The dashboard is built against generated trades (P7) so it can be developed before
real sales exist. This column is what keeps that safe: synthetic rows never count
toward the provenance register, so seeding cannot close a placeholder, and the
dashboard marks them on screen rather than letting them read as results.

Revision ID: 0008_synthetic_flag
Revises: 0007_autobuy_rails
Create Date: 2026-08-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_synthetic_flag"
down_revision: str | Sequence[str] | None = "0007_autobuy_rails"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("inventory", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("synthetic", sa.Boolean(), server_default="0", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("inventory", schema=None) as batch_op:
        batch_op.drop_column("synthetic")
