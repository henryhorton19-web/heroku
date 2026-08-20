"""AutoBuy rails: idempotent purchase attempts and the dead-man switch.

Two tables, both of which exist to make a failure fail closed.

`purchase_attempts.idempotency_key` is UNIQUE. A retry after a crash is refused by the
database rather than by application logic a retry path might skip, because an AutoBuy
without this double-buys and you find out when two identical jumpers arrive.

`autobuy_state.armed_until` is an expiry rather than a boolean. AutoBuy requires
periodic affirmative action to keep running, so walking away from the machine stops it.

Revision ID: 0007_autobuy_rails
Revises: 0006_monitor_runs
Create Date: 2026-08-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from arb.db import UtcDateTime

revision: str = "0007_autobuy_rails"
down_revision: str | Sequence[str] | None = "0006_monitor_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "purchase_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("venue", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=True),
        sa.Column("spend_pence", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempted_at", UtcDateTime(), nullable=False),
        sa.Column("completed_at", UtcDateTime(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("idx_attempts_time", "purchase_attempts", ["attempted_at"])
    op.create_table(
        "autobuy_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("armed_until", UtcDateTime(), nullable=True),
        sa.Column("kill_switch", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("updated_at", UtcDateTime(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("autobuy_state")
    op.drop_index("idx_attempts_time", table_name="purchase_attempts")
    op.drop_table("purchase_attempts")
