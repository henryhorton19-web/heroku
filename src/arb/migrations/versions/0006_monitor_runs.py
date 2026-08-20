"""Monitor heartbeat.

A monitor that has stopped working looks exactly like a quiet market: both produce no
alerts. This table is what distinguishes them. Rows are written on failure as well as
success, because a crashed run that leaves no trace is indistinguishable from a run
that never started.

Revision ID: 0006_monitor_runs
Revises: 0005_sweep_columns
Create Date: 2026-08-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from arb.db import UtcDateTime

revision: str = "0006_monitor_runs"
down_revision: str | Sequence[str] | None = "0005_sweep_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitor_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("monitor", sa.String(), nullable=False),
        sa.Column("started_at", UtcDateTime(), nullable=False),
        sa.Column("finished_at", UtcDateTime(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("listings_seen", sa.Integer(), nullable=False),
        sa.Column("new_listings", sa.Integer(), nullable=False),
        sa.Column("ranked", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_monitor_runs", "monitor_runs", ["monitor", "started_at"])


def downgrade() -> None:
    op.drop_index("idx_monitor_runs", table_name="monitor_runs")
    op.drop_table("monitor_runs")
