"""enforce campaign owner constraint

Revision ID: ff0af9932375
Revises: 3e1ec57a8a28
Create Date: 2026-02-01 16:08:19.902535
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa



# revision identifiers, used by Alembic.
revision = 'ff0af9932375'
down_revision = '3e1ec57a8a28'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_campaign_owner_exactly_one",
        "campaigns",
        "(business_id IS NOT NULL AND influencer_id IS NULL) OR (business_id IS NULL AND influencer_id IS NOT NULL)",
    )

    op.alter_column(
        "campaigns",
        "campaign_type",
        existing_type=sa.String(length=30),
        server_default=sa.text("'business'"),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "campaigns",
        "campaign_type",
        existing_type=sa.String(length=30),
        server_default=None,
        existing_nullable=False,
    )
    op.drop_constraint("ck_campaign_owner_exactly_one", "campaigns", type_="check")

