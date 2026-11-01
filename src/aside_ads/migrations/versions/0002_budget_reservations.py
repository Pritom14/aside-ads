"""budget reservations

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-01 23:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "budget_reservations",
        sa.Column("request_id", sa.String(length=32), nullable=False),
        sa.Column("campaign_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=3), nullable=False),
        sa.Column("cost_micros", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("request_id"),
    )
    with op.batch_alter_table("budget_reservations", schema=None) as batch_op:
        batch_op.create_index(
            "ix_budget_reservations_campaign_expires", ["campaign_id", "expires_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_budget_reservations_expires_at"), ["expires_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("budget_reservations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_budget_reservations_expires_at"))
        batch_op.drop_index("ix_budget_reservations_campaign_expires")
    op.drop_table("budget_reservations")
