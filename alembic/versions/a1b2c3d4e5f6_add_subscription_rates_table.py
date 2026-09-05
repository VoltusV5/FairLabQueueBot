"""add subscription_rates table

Revision ID: a1b2c3d4e5f6
Revises: 21ba42f81c2b
Create Date: 2026-07-28 15:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "21ba42f81c2b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subscription_rates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tier", sa.String(length=32), nullable=False),
        sa.Column(
            "amount_rub", sa.Numeric(precision=10, scale=2), nullable=False
        ),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tier", name="uq_subscription_rates_tier"),
    )

    # Seed initial rates from billing_constants defaults
    op.bulk_insert(
        sa.table(
            "subscription_rates",
            sa.column("tier", sa.String),
            sa.column("amount_rub", sa.Numeric),
            sa.column("description", sa.String),
            sa.column("updated_at", sa.DateTime),
        ),
        [
            {
                "tier": "trial",
                "amount_rub": "0.00",
                "description": "Trial tier (free)",
                "updated_at": sa.func.now(),
            },
            {
                "tier": "base",
                "amount_rub": "149.00",
                "description": "Base subscription tier",
                "updated_at": sa.func.now(),
            },
            {
                "tier": "supervip",
                "amount_rub": "249.00",
                "description": "SuperVIP subscription tier",
                "updated_at": sa.func.now(),
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("subscription_rates")
