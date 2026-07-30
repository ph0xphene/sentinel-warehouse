"""Add protocol attribution to invariants and incidents.

Revision ID: 20260730_0007
Revises: 20260730_0006
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0007"
down_revision: str | None = "20260730_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invariants",
        sa.Column("protocol_name", sa.String(length=100), nullable=True),
        schema="security",
    )
    op.add_column(
        "incidents",
        sa.Column("protocol_name", sa.String(length=100), nullable=True),
        schema="security",
    )


def downgrade() -> None:
    op.drop_column("incidents", "protocol_name", schema="security")
    op.drop_column("invariants", "protocol_name", schema="security")
